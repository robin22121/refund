from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_mock_flow():
    session = client.post(
        "/auth/start",
        json={"name": "홍길동", "birth_date": "1965-03-02", "phone": "01000000000"},
    ).json()["session_id"]
    res = client.post(f"/diagnosis/{session}")
    assert res.status_code == 200
    assert len(res.json()["findings"]) >= 1
