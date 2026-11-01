from pathlib import Path

from fastapi import FastAPI, HTTPException
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
from .collectors import EasyAuthRequest, get_collector
from .models import DiagnosisReport, TaxpayerData
from .registry import MockRegistryFetcher, search_addresses
from .rules import run_diagnosis

app = FastAPI(title="부동산 세금 환급 진단 API")
collector = get_collector()
registry = MockRegistryFetcher()
store = CaseStore()


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
    case.status = CaseStatus.DIAGNOSED
    store.save(case)
    return report


@app.post("/diagnosis", response_model=DiagnosisReport)
def diagnose_raw(data: TaxpayerData) -> DiagnosisReport:
    """자료를 직접 넣어 진단한다. 룰 검증과 세무사 테스트용."""
    return run_diagnosis(data)


# 모바일 웹 화면. API 경로가 먼저 매칭되도록 맨 마지막에 붙인다.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
