from fastapi import FastAPI, HTTPException

from .collectors import EasyAuthRequest, get_collector
from .models import DiagnosisReport, TaxpayerData
from .rules import run_diagnosis

app = FastAPI(title="부동산 세금 환급 진단 API")
collector = get_collector()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/auth/start")
def start_auth(req: EasyAuthRequest) -> dict:
    """간편인증 요청. 사용자가 휴대폰에서 승인한 뒤 /diagnosis/{session_id}를 부른다."""
    try:
        return {"session_id": collector.start_auth(req)}
    except NotImplementedError as e:
        raise HTTPException(501, str(e))


@app.post("/diagnosis/{session_id}", response_model=DiagnosisReport)
def diagnose(session_id: str) -> DiagnosisReport:
    try:
        data = collector.collect(session_id)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    return run_diagnosis(data)


@app.post("/diagnosis", response_model=DiagnosisReport)
def diagnose_raw(data: TaxpayerData) -> DiagnosisReport:
    """자료를 직접 넣어 진단한다. 룰 검증과 세무사 테스트용."""
    return run_diagnosis(data)
