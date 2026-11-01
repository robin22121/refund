import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
    advisor_view,
    customer_view,
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
from .security import (
    SECURITY_HEADERS,
    AuditLog,
    FailureLimiter,
    advisor_key_from_env,
    hash_token,
    is_production,
    new_token,
    token_matches,
)

app = FastAPI(title="부동산 세금 환급 진단 API")
collector = get_collector()
registry = MockRegistryFetcher()
store = CaseStore()
analyst = get_analyst()
ledger = Ledger()
gateway = MockPaymentGateway()
# 세무사 화면 접근 키. 운영 환경에서는 기본값을 쓸 수 없다. TODO: 세무사별 계정·2단계 인증으로 교체
ADVISOR_KEY = advisor_key_from_env()
advisor_limiter = FailureLimiter()
audit = AuditLog()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if not request.url.path.endswith((".js", ".css", ".png", ".svg", ".ico")):
        response.headers["Cache-Control"] = "no-store"
    if is_production():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _case(case_id: str) -> Case:
    case = store.get(case_id)
    if case is None:
        raise HTTPException(404, "접수 건을 찾을 수 없습니다")
    return case


def customer_case(case_id: str, x_case_token: str = Header("")) -> Case:
    """고객 API: 접수할 때 받은 토큰이 맞아야 그 건을 볼 수 있다."""
    case = _case(case_id)
    if not token_matches(x_case_token, case.access_token_hash):
        raise HTTPException(403, "이 접수 건에 접근할 수 없습니다. 처음부터 다시 진행해 주세요.")
    return case


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/cases")
def create_case(req: CaseCreate) -> dict:
    """1~2단계: 소재지와 검토 희망 항목을 받고 등기부 등을 자동 조회한다.

    응답의 access_token은 이때 한 번만 내려 준다. 이후 고객 API는 X-Case-Token 헤더로 보낸다.
    """
    token = new_token()
    case = Case(applicant=req, access_token_hash=hash_token(token))
    case.assets = [registry.lookup(a, req.name) for a in req.addresses]
    store.save(case)
    return customer_view(case) | {"access_token": token}


@app.get("/address/search")
def address_search(q: str) -> list[dict]:
    """소재지 입력을 돕는 주소 검색. 고령 사용자가 주소를 끝까지 치지 않아도 되게 한다."""
    return search_addresses(q)


@app.put("/cases/{case_id}/concern")
def update_concern(req: ConcernUpdate, case: Case = Depends(customer_case)) -> dict:
    """2단계: 자동 조회 결과를 본 뒤 검토 희망 항목을 적는다."""
    case.applicant.concern = req.concern
    case.concern_rule_ids = req.concern_rule_ids
    return customer_view(store.save(case))


@app.get("/cases/{case_id}")
def get_case(case: Case = Depends(customer_case)) -> dict:
    return customer_view(case)


@app.get("/mandate/scopes")
def mandate_scopes() -> list[str]:
    """전자위임장에 표시할 위임·동의 항목."""
    return MANDATE_SCOPES


@app.get("/mandate/info")
def mandate_info() -> dict:
    """전자위임장 화면에 표시할 수임 세무사와 위임 항목."""
    return {"agent": MANDATE_AGENT, "scopes": MANDATE_SCOPES}


@app.post("/cases/{case_id}/mandate")
def sign(req: MandateSign, request: Request, case: Case = Depends(customer_case)) -> dict:
    """3단계: 전자위임장 서명."""
    try:
        return customer_view(store.save(sign_mandate(case, req, signer_ip=_ip(request))))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/cases/{case_id}/auth/start")
def start_auth(req: AuthStart, case: Case = Depends(customer_case)) -> dict:
    """4단계: 위임장 서명이 끝난 건만 홈택스 간편인증을 시작한다."""
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
    return customer_view(store.save(case))


@app.post("/cases/{case_id}/diagnosis", response_model=DiagnosisReport)
def diagnose(case: Case = Depends(customer_case)) -> DiagnosisReport:
    """사용자가 휴대폰에서 인증을 승인한 뒤 호출. 자료를 모아 진단한다."""
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


def advisor(request: Request, x_advisor_key: str = Header("")) -> dict:
    who = _ip(request)
    if advisor_limiter.blocked(who):
        raise HTTPException(429, "접근 키를 여러 번 틀려 잠시 막았습니다. 10분 뒤 다시 시도해 주세요.")
    if not secrets.compare_digest(x_advisor_key.encode(), ADVISOR_KEY.encode()):
        advisor_limiter.fail(who)
        audit.record("unknown", "advisor_key_failed", None, who)
        raise HTTPException(401, "세무사 접근 키가 맞지 않습니다")
    advisor_limiter.reset(who)
    request.state.actor = MANDATE_AGENT["name"]
    return MANDATE_AGENT


def _audit(request: Request, action: str, case_id: str | None = None) -> None:
    audit.record(getattr(request.state, "actor", "?"), action, case_id, _ip(request))


@app.post("/diagnosis", response_model=DiagnosisReport)
def diagnose_raw(data: TaxpayerData, _: dict = Depends(advisor)) -> DiagnosisReport:
    """자료를 직접 넣어 진단한다. 룰 검증과 세무사 테스트용이라 세무사 키가 필요하다."""
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
def request_claim(req: ClaimRequest, case: Case = Depends(customer_case)) -> dict:
    """진단 뒤 고객이 수수료 약정에 동의하고 세무사에게 맡긴다. AI 전략 초안을 이때 만든다."""
    case = _run(workflow.request_claim, case, req, analyst)
    store.save(case)
    return workflow.progress(case, ledger)


@app.get("/cases/{case_id}/progress")
def case_progress(case: Case = Depends(customer_case)) -> dict:
    return workflow.progress(case, ledger)


@app.post("/cases/{case_id}/payment")
def pay(req: PaymentRequest, case: Case = Depends(customer_case)) -> dict:
    """환급이 결정된 뒤 성공보수를 결제한다 (가짜 PG)."""
    case = _run(workflow.pay_success_fee, case, req.method, ledger, gateway)
    store.save(case)
    return workflow.progress(case, ledger)


# ---------- 세무사: 검토 · 제출 · 결과 기록 ----------

def _advisor_case(case_id: str) -> Case:
    case = _case(case_id)
    if case.requested_at is None:
        raise HTTPException(404, "고객이 아직 신청하지 않은 건입니다")
    return case


@app.get("/advisor/cases")
def advisor_cases(request: Request, _: dict = Depends(advisor)) -> list[dict]:
    """세무사에게 맡겨진 건 목록. 마감이 급한 순서."""
    _audit(request, "list_cases")
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


@app.get("/advisor/cases/{case_id}")
def advisor_case(case_id: str, request: Request, _: dict = Depends(advisor)) -> dict:
    case = _advisor_case(case_id)
    _audit(request, "view_case", case_id)
    return advisor_view(case)


@app.post("/advisor/cases/{case_id}/strategy")
def regenerate_strategy(case_id: str, request: Request, _: dict = Depends(advisor)) -> dict:
    """AI 초안을 다시 만든다. 세무사가 고친 검토 내용은 초기화된다."""
    case = _advisor_case(case_id)
    if case.status not in (CaseStatus.REQUESTED, CaseStatus.REVIEWING):
        raise HTTPException(409, "제출한 뒤에는 초안을 다시 만들 수 없습니다")
    _audit(request, "regenerate_strategy", case_id)
    return advisor_view(store.save(workflow.generate_strategy(case, analyst)))


@app.put("/advisor/cases/{case_id}/review")
def save_review(case_id: str, req: ReviewUpdate, request: Request, _: dict = Depends(advisor)) -> dict:
    case = store.save(_run(workflow.save_review, _advisor_case(case_id), req))
    _audit(request, "save_review", case_id)
    return advisor_view(case)


@app.post("/advisor/cases/{case_id}/file")
def file_claim(case_id: str, request: Request, _: dict = Depends(advisor)) -> dict:
    """경정청구서 제출 (가짜 홈택스·위택스). 플랫폼 이용료가 이때 청구된다."""
    case = store.save(_run(workflow.file_claim, _advisor_case(case_id), ledger, gateway))
    _audit(request, "file_claim", case_id)
    return advisor_view(case)


@app.post("/advisor/cases/{case_id}/decision")
def record_decision(case_id: str, req: DecisionInput, request: Request, _: dict = Depends(advisor)) -> dict:
    """세무서 결과 통지를 기록한다."""
    case = store.save(_run(workflow.record_decision, _advisor_case(case_id), req))
    _audit(request, "record_decision", case_id)
    return advisor_view(case)


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
