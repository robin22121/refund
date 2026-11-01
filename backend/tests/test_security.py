"""출시 전 보안 점검 항목 테스트."""
import base64

import pytest
from fastapi.testclient import TestClient

from app import main
from app.cases import Case, CaseCreate, CaseStore
from app.security import Cipher, ConfigError, FailureLimiter, mask_phone, mask_rrn, normalize_rrn
from tests.helpers import SIGNATURE

raw = TestClient(main.app)
APPLICANT = {
    "name": "홍길동",
    "birth_date": "1965-03-02",
    "phone": "01012345678",
    "addresses": ["서울특별시 마포구 성미산로 77"],
}
ADV = {"X-Advisor-Key": main.ADVISOR_KEY}


def _create():
    body = raw.post("/cases", json=APPLICANT).json()
    return body["id"], {"X-Case-Token": body["access_token"]}


def test_case_requires_its_own_token():
    cid, tok = _create()
    other, other_tok = _create()
    assert raw.get(f"/cases/{cid}").status_code == 403
    assert raw.get(f"/cases/{cid}", headers=other_tok).status_code == 403
    assert raw.get(f"/cases/{cid}/progress").status_code == 403
    assert raw.post(f"/cases/{cid}/diagnosis").status_code == 403
    assert raw.get(f"/cases/{cid}", headers=tok).status_code == 200


def test_token_not_stored_in_plain_and_not_returned_again():
    cid, tok = _create()
    body = raw.get(f"/cases/{cid}", headers=tok).json()
    assert "access_token" not in body and "access_token_hash" not in body
    assert tok["X-Case-Token"] not in main.store.raw(cid)


def test_store_encrypts_personal_data_at_rest():
    cid, _ = _create()
    blob = main.store.raw(cid)
    assert blob.startswith("v1:")
    for plain in ("홍길동", "01012345678", "1965-03-02", "성미산로"):
        assert plain not in blob and plain.encode() not in base64.b64decode(blob[3:])


def test_store_rejects_swapped_ciphertext():
    store = CaseStore(Cipher(b"k" * 32))
    a = store.save(Case(applicant=CaseCreate(**APPLICANT)))
    b = store.save(Case(applicant=CaseCreate(**APPLICANT)))
    store._cases[a.id] = store._cases[b.id]  # 다른 건 암호문을 끼워 넣으면 복호화가 실패해야 한다
    with pytest.raises(Exception):
        store.get(a.id)


def test_customer_view_hides_internal_fields_and_signature():
    cid, tok = _create()
    scopes = raw.get("/mandate/scopes").json()
    signed = raw.post(f"/cases/{cid}/mandate", headers=tok,
                      json={"signer_name": "홍길동", "agreed_scopes": scopes, "signature_image": SIGNATURE}).json()
    assert "signature_image" not in signed["mandate"]
    assert len(signed["mandate"]["document_hash"]) == 64
    assert signed["mandate"]["agent"]["registration_no"]
    raw.post(f"/cases/{cid}/auth/start", headers=tok, json={})
    raw.post(f"/cases/{cid}/diagnosis", headers=tok)
    raw.post(f"/cases/{cid}/request", headers=tok, json={"agree_fee_terms": True})
    n = len(raw.get(f"/advisor/cases/{cid}", headers=ADV).json()["strategy"]["items"])
    raw.put(f"/advisor/cases/{cid}/review", headers=ADV, json={
        "items": [{"index": i, "decision": "보류"} for i in range(n)],
        "claim_statement": "x", "advisor_memo": "고객이 말을 바꿀 수 있음",
    })
    body = raw.get(f"/cases/{cid}", headers=tok).json()
    for hidden in ("data", "strategy", "review", "auth_session_id"):
        assert hidden not in body
    assert "고객이 말을 바꿀 수 있음" not in raw.get(f"/cases/{cid}", headers=tok).text
    assert "고객이 말을 바꿀 수 있음" not in raw.get(f"/cases/{cid}/progress", headers=tok).text
    adv = raw.get(f"/advisor/cases/{cid}", headers=ADV).json()
    assert adv["review"]["advisor_memo"] and "signature_image" not in adv["mandate"]


def test_mandate_rejects_bad_signature_and_resign_after_auth():
    cid, tok = _create()
    scopes = raw.get("/mandate/scopes").json()
    bad = {"signer_name": "홍길동", "agreed_scopes": scopes, "signature_image": "<svg onload=alert(1)>"}
    assert raw.post(f"/cases/{cid}/mandate", headers=tok, json=bad).status_code == 422
    huge = {**bad, "signature_image": "data:image/png;base64," + "A" * 500_000}
    assert raw.post(f"/cases/{cid}/mandate", headers=tok, json=huge).status_code == 422
    ok = {**bad, "signature_image": SIGNATURE}
    assert raw.post(f"/cases/{cid}/mandate", headers=tok, json=ok).status_code == 200
    raw.post(f"/cases/{cid}/auth/start", headers=tok, json={})
    assert raw.post(f"/cases/{cid}/mandate", headers=tok, json=ok).status_code == 422


def test_raw_diagnosis_needs_advisor_key():
    body = {"name": "가", "birth_date": "1960-01-01"}
    assert raw.post("/diagnosis", json=body).status_code == 401
    assert raw.post("/diagnosis", json=body, headers=ADV).status_code == 200


def test_advisor_key_lockout_and_audit(monkeypatch):
    monkeypatch.setattr(main, "advisor_limiter", FailureLimiter(max_failures=3))
    for _ in range(3):
        assert raw.get("/advisor/cases", headers={"X-Advisor-Key": "wrong"}).status_code == 401
    assert raw.get("/advisor/cases", headers=ADV).status_code == 429
    assert any(e.action == "advisor_key_failed" for e in main.audit.entries)


def test_advisor_access_is_audited():
    before = len(main.audit.entries)
    raw.get("/advisor/cases", headers=ADV)
    assert main.audit.entries[before:] and main.audit.entries[-1].action == "list_cases"


def test_security_headers():
    res = raw.get("/health")
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]


def test_production_refuses_demo_secrets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ADVISOR_KEY", raising=False)
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app import security
    with pytest.raises(ConfigError):
        security.advisor_key_from_env()
    with pytest.raises(ConfigError):
        security.Cipher.from_env()
    monkeypatch.setenv("ADVISOR_KEY", "x" * 40)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    assert security.advisor_key_from_env() == "x" * 40
    security.Cipher.from_env()


def test_rrn_helpers():
    assert normalize_rrn("6503021234567") == "650302-1234567"
    assert mask_rrn("650302-1234567") == "650302-1******"
    assert mask_phone("010-1234-5678") == "010-****-5678"
    with pytest.raises(ValueError):
        normalize_rrn("650302-9234567")
