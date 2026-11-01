from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

APPLICANT = {
    "name": "홍길동",
    "birth_date": "1965-03-02",
    "phone": "01000000000",
    "addresses": ["서울특별시 마포구 예시로 1", "부산 어딘가 999"],
    "concern": "2023년에 판 마포 아파트 양도세가 너무 많이 나왔어요",
}


def _create():
    res = client.post("/cases", json=APPLICANT)
    assert res.status_code == 200
    return res.json()


def _sign(case_id, scopes=None):
    scopes = scopes if scopes is not None else client.get("/mandate/scopes").json()
    return client.post(
        f"/cases/{case_id}/mandate",
        json={"signer_name": "홍길동", "agreed_scopes": scopes, "signature_image": "data:image/png;base64,AAAA"},
    )


def test_case_flow_end_to_end():
    case = _create()
    assert case["assets"][0]["property"]["id"] == "apt-1"
    assert case["assets"][1]["error"]

    assert _sign(case["id"]).json()["status"] == "위임완료"
    assert client.post(f"/cases/{case['id']}/auth/start", json={"provider": "kakao"}).json()["status"] == "인증중"

    report = client.post(f"/cases/{case['id']}/diagnosis").json()
    assert report["concern"] == APPLICANT["concern"]
    assert any(f["property_id"] == "apt-1" for f in report["findings"])


def test_auth_blocked_before_mandate():
    case = _create()
    res = client.post(f"/cases/{case['id']}/auth/start", json={})
    assert res.status_code == 409


def test_mandate_requires_all_scopes():
    case = _create()
    res = _sign(case["id"], scopes=[])
    assert res.status_code == 422
