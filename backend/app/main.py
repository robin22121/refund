import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from .cases import (
    MANDATE_AGENT,
    MANDATE_SCOPES,
    AuthStart,
    Case,
    CaseCreate,
    ConcernUpdate,
    CaseStatus,
    CaseStore,
    MandateSign,
    sign_mandate,
)
from . import workflow
from .analysis import get_analyst
from .billing import PLATFORM_FEE_PER_CASE, SUCCESS_FEE_RATE, Ledger, MockPaymentGateway, fee_terms
from .claims import ClaimRequest, DecisionInput, PaymentRequest, ReviewUpdate
from .collectors import EasyAuthRequest, get_collector
from .models import DiagnosisReport, TaxpayerData
from .registry import MockRegistryFetcher, search_addresses
from .rules import run_diagnosis

app = FastAPI(title="부동산 세금 환급 진단 API")
collector = get_collector()
registry = MockRegistryFetcher()
store = CaseStore()
analyst = get_analyst()
ledger = Ledger()
gateway = MockPaymentGateway()
# 세무사 화면 접근 키. TODO: 세무사 계정·로그인으로 교체
ADVISOR_KEY = os.getenv("ADVISOR_KEY", "demo-advisor")


def _case(case_id: str) -> Case:
    case = store.get(case_id)
    if case is None:
        raise HTTPException(404, "접수 건을 찾을 수 없습니다")
    return case


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/cases", response_model=Case)
def create_case(req: CaseCreate) -> Case:
    """1~2단계: 소재지와 검토 희망 항목을 받고 등기부 등을 자동 조회한다."""
    case = Case(applicant=req)
    case.assets = [registry.lookup(a, req.name) for a in req.addresses]
    return store.save(case)


@app.get("/address/search")
def address_search(q: str) -> list[dict]:
    """소재지 입력을 돕는 주소 검색. 고령 사용자가 주소를 끝까지 치지 않아도 되게 한다."""
    return search_addresses(q)


@app.put("/cases/{case_id}/concern", response_model=Case)
def update_concern(case_id: str, req: ConcernUpdate) -> Case:
    """2단계: 자동 조회 결과를 본 뒤 검토 희망 항목을 적는다."""
    case = _case(case_id)
    case.applicant.concern = req.concern
    case.concern_rule_ids = req.concern_rule_ids
    return store.save(case)


@app.get("/cases/{case_id}", response_model=Case)
def get_case(case_id: str) -> Case:
    return _case(case_id)


@app.get("/mandate/scopes")
def mandate_scopes() -> list[str]:
    """전자위임장에 표시할 위임·동의 항목."""
    return MANDATE_SCOPES


@app.get("/mandate/info")
def mandate_info() -> dict:
    """전자위임장 화면에 표시할 수임 세무사와 위임 항목."""
    return {"agent": MANDATE_AGENT, "scopes": MANDATE_SCOPES}


@app.post("/cases/{case_id}/mandate", response_model=Case)
def sign(case_id: str, req: MandateSign) -> Case:
    """3단계: 전자위임장 서명."""
    case = _case(case_id)
    try:
        return store.save(sign_mandate(case, req))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/cases/{case_id}/auth/start", response_model=Case)
def start_auth(case_id: str, req: AuthStart) -> Case:
    """4단계: 위임장 서명이 끝난 건만 홈택스 간편인증을 시작한다."""
    case = _case(case_id)
    if case.mandate is None:
        raise HTTPException(409, "전자위임장 서명 후 인증할 수 있습니다")
    a = case.applicant
    try:
        case.auth_session_id = collector.start_auth(
            EasyAuthRequest(name=a.name, birth_date=a.birth_date, phone=a.phone, provider=req.provider)
        )
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    case.status = CaseStatus.AUTHENTICATING
    return store.save(case)


@app.post("/cases/{case_id}/diagnosis", response_model=DiagnosisReport)
def diagnose(case_id: str) -> DiagnosisReport:
    """사용자가 휴대폰에서 인증을 승인한 뒤 호출. 자료를 모아 진단한다."""
    case = _case(case_id)
    if case.auth_session_id is None:
        raise HTTPException(409, "홈택스 인증을 먼저 진행해 주세요")
    try:
        data = collector.collect(case.auth_session_id)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    # 소재지로 조회한 등기부 정보를 우선한다
    by_id = {p.id: p for p in data.properties}
    for asset in case.assets:
        if asset.property:
            by_id[asset.property.id] = asset.property
    data.properties = list(by_id.values())
    report = run_diagnosis(data)
    report.concern = case.applicant.concern
    case.report = report
    case.data = data
    case.status = CaseStatus.DIAGNOSED
    store.save(case)
    return report


@app.post("/diagnosis", response_model=DiagnosisReport)
def diagnose_raw(data: TaxpayerData) -> DiagnosisReport:
    """자료를 직접 넣어 진단한다. 룰 검증과 세무사 테스트용."""
    return run_diagnosis(data)


def _run(fn, *args):
    try:
        return fn(*args)
    except workflow.WorkflowError as e:
        raise HTTPException(e.status, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------- 고객: 신청 · 진행 상황 · 성공보수 결제 ----------

@app.get("/fees/terms")
def get_fee_terms() -> dict:
    """신청 화면에 보여 줄 수수료 약정."""
    return fee_terms()


@app.post("/cases/{case_id}/request")
def request_claim(case_id: str, req: ClaimRequest) -> dict:
    """진단 뒤 고객이 수수료 약정에 동의하고 세무사에게 맡긴다. AI 전략 초안을 이때 만든다."""
    case = _run(workflow.request_claim, _case(case_id), req, analyst)
    store.save(case)
    return workflow.progress(case, ledger)


@app.get("/cases/{case_id}/progress")
def case_progress(case_id: str) -> dict:
    return workflow.progress(_case(case_id), ledger)


@app.post("/cases/{case_id}/payment")
def pay(case_id: str, req: PaymentRequest) -> dict:
    """환급이 결정된 뒤 성공보수를 결제한다 (가짜 PG)."""
    case = _run(workflow.pay_success_fee, _case(case_id), req.method, ledger, gateway)
    store.save(case)
    return workflow.progress(case, ledger)


# ---------- 세무사: 검토 · 제출 · 결과 기록 ----------

def advisor(x_advisor_key: str = Header("")) -> dict:
    if not secrets.compare_digest(x_advisor_key, ADVISOR_KEY):
        raise HTTPException(401, "세무사 접근 키가 맞지 않습니다")
    return MANDATE_AGENT


def _advisor_case(case_id: str) -> Case:
    case = _case(case_id)
    if case.requested_at is None:
        raise HTTPException(404, "고객이 아직 신청하지 않은 건입니다")
    return case


@app.get("/advisor/cases")
def advisor_cases(_: dict = Depends(advisor)) -> list[dict]:
    """세무사에게 맡겨진 건 목록. 마감이 급한 순서."""
    rows = []
    for c in store.all():
        if c.requested_at is None:
            continue
        deadlines = [f.claim_deadline for f in c.report.findings if f.claim_deadline]
        rows.append({
            "id": c.id,
            "name": c.applicant.name,
            "status": c.status,
            "requested_at": c.requested_at,
            "concern": c.applicant.concern,
            "findings": len(c.report.findings),
            "estimated_refund": sum(f.estimated_refund or 0 for f in c.report.findings),
            "nearest_deadline": min(deadlines) if deadlines else None,
            "ai_model": c.strategy.model if c.strategy else None,
            "ai_error": c.strategy_error,
        })
    rows.sort(key=lambda r: (str(r["nearest_deadline"] or "9999"), str(r["requested_at"])))
    return rows


@app.get("/advisor/cases/{case_id}", response_model=Case)
def advisor_case(case_id: str, _: dict = Depends(advisor)) -> Case:
    return _advisor_case(case_id)


@app.post("/advisor/cases/{case_id}/strategy", response_model=Case)
def regenerate_strategy(case_id: str, _: dict = Depends(advisor)) -> Case:
    """AI 초안을 다시 만든다. 세무사가 고친 검토 내용은 초기화된다."""
    case = _advisor_case(case_id)
    if case.status not in (CaseStatus.REQUESTED, CaseStatus.REVIEWING):
        raise HTTPException(409, "제출한 뒤에는 초안을 다시 만들 수 없습니다")
    return store.save(workflow.generate_strategy(case, analyst))


@app.put("/advisor/cases/{case_id}/review", response_model=Case)
def save_review(case_id: str, req: ReviewUpdate, _: dict = Depends(advisor)) -> Case:
    return store.save(_run(workflow.save_review, _advisor_case(case_id), req))


@app.post("/advisor/cases/{case_id}/file", response_model=Case)
def file_claim(case_id: str, _: dict = Depends(advisor)) -> Case:
    """경정청구서 제출 (가짜 홈택스·위택스). 플랫폼 이용료가 이때 청구된다."""
    return store.save(_run(workflow.file_claim, _advisor_case(case_id), ledger, gateway))


@app.post("/advisor/cases/{case_id}/decision", response_model=Case)
def record_decision(case_id: str, req: DecisionInput, _: dict = Depends(advisor)) -> Case:
    """세무서 결과 통지를 기록한다."""
    return store.save(_run(workflow.record_decision, _advisor_case(case_id), req))


@app.get("/advisor/billing")
def advisor_billing(agent: dict = Depends(advisor)) -> dict:
    """세무사 정산: 받은 성공보수와 플랫폼에 낼 이용료."""
    pays = ledger.payments
    return {
        "agent": agent,
        "success_fee_rate": SUCCESS_FEE_RATE,
        "platform_fee_per_case": PLATFORM_FEE_PER_CASE,
        "success_fees": sum(p.amount for p in pays if p.purpose == "success_fee"),
        "platform_fees": sum(p.amount for p in pays if p.purpose == "platform_fee"),
        "payments": sorted(pays, key=lambda p: p.approved_at, reverse=True),
    }


# 모바일 웹 화면. API 경로가 먼저 매칭되도록 맨 마지막에 붙인다.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
