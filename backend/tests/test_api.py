from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

APPLICANT = {
    "name": "홍길동",
    "birth_date": "1965-03-02",
    "phone": "01000000000",
    "addresses": ["서울특별시 마포구 성미산로 77", "부산 어딘가 999"],
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


def test_address_search_and_concern_update():
    hits = client.get("/address/search", params={"q": "마포 성미산로"}).json()
    assert hits and hits[0]["address"].startswith("서울특별시 마포구 성미산로 77")
    assert client.get("/address/search", params={"q": "없는동네"}).json() == []

    case = client.post("/cases", json={**APPLICANT, "addresses": [hits[0]["address"]], "concern": ""}).json()
    assert case["assets"][0]["documents"][0] == "등기사항전부증명서"
    res = client.put(f"/cases/{case['id']}/concern", json={"concern": "비과세를 못 받았어요"})
    assert res.json()["applicant"]["concern"] == "비과세를 못 받았어요"


def test_mock_report_uses_applicant_name_and_addresses():
    case = client.post("/cases", json={**APPLICANT, "name": "김영숙"}).json()
    client.post(
        f"/cases/{case['id']}/mandate",
        json={"signer_name": "김영숙", "agreed_scopes": MANDATE, "signature_image": "data:,"},
    )
    client.post(f"/cases/{case['id']}/auth/start", json={"provider": "kakao"})
    report = client.post(f"/cases/{case['id']}/diagnosis").json()
    assert report["taxpayer_name"] == "김영숙"
    assert all(f["property_address"] for f in report["findings"])


def test_frontend_served():
    res = client.get("/")
    assert res.status_code == 200 and "양도세" in res.text


MANDATE = client.get("/mandate/info").json()["scopes"]
