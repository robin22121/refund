import json
from types import SimpleNamespace

from tests.helpers import SIGNATURE, TokenClient

from app.analysis import AnalysisInput, MockAnalyst, analyze
from app.analysis.claude import OUTPUT_SCHEMA, ClaudeAnalyst
from app.collectors.mock import sample_taxpayer
from app.main import ADVISOR_KEY, app
from app.rules import run_diagnosis

client = TokenClient(app)
ADV = {"X-Advisor-Key": ADVISOR_KEY}

APPLICANT = {
    "name": "홍길동",
    "birth_date": "1965-03-02",
    "phone": "01000000000",
    "addresses": ["서울특별시 마포구 성미산로 77"],
    "concern": "",
}


def _diagnosed_case():
    case = client.post("/cases", json=APPLICANT).json()
    cid = case["id"]
    client.put(f"/cases/{cid}/concern", json={"concern": "공제가 적어요", "concern_rule_ids": ["CGT_LTHD_RESIDENCE"]})
    scopes = client.get("/mandate/scopes").json()
    client.post(f"/cases/{cid}/mandate", json={"signer_name": "홍길동", "agreed_scopes": scopes, "signature_image": SIGNATURE})
    client.post(f"/cases/{cid}/auth/start", json={"provider": "kakao"})
    client.post(f"/cases/{cid}/diagnosis")
    return cid


def test_full_claim_flow():
    cid = _diagnosed_case()
    assert client.post(f"/cases/{cid}/request", json={"agree_fee_terms": False}).status_code == 422
    prog = client.post(f"/cases/{cid}/request", json={"agree_fee_terms": True}).json()
    assert prog["status"] == "신청접수"
    assert prog["summary"]
    assert [s["state"] for s in prog["steps"]][:2] == ["done", "now"]

    assert client.get("/advisor/cases").status_code == 401
    rows = client.get("/advisor/cases", headers=ADV).json()
    assert any(r["id"] == cid for r in rows)

    case = client.get(f"/advisor/cases/{cid}", headers=ADV).json()
    items = case["strategy"]["items"]
    assert items and case["strategy"]["model"] == "mock"
    assert all(it["legal_basis"] for it in items)

    # 채택 하나, 나머지 제외 전에 보류가 남아 있으면 제출할 수 없다
    review = {
        "items": [{"index": 0, "decision": "채택", "refund": 10_000_000}]
        + [{"index": i, "decision": "보류"} for i in range(1, len(items))],
        "claim_statement": "1. 청구 이유",
        "customer_message": "중개수수료 영수증을 보내 주세요",
    }
    assert client.put(f"/advisor/cases/{cid}/review", json=review, headers=ADV).status_code == 200
    if len(items) > 1:
        assert client.post(f"/advisor/cases/{cid}/file", headers=ADV).status_code == 422
    for r in review["items"][1:]:
        r["decision"] = "제외"
    client.put(f"/advisor/cases/{cid}/review", json=review, headers=ADV)
    assert client.get(f"/cases/{cid}/progress").json()["message"] == "중개수수료 영수증을 보내 주세요"

    filed = client.post(f"/advisor/cases/{cid}/file", headers=ADV).json()
    assert filed["status"] == "청구제출"
    assert filed["filing"]["claimed_refund"] == 10_000_000
    assert filed["filing"]["submissions"][0]["channel"] == "홈택스"

    assert client.post(f"/cases/{cid}/payment", json={"method": "card"}).status_code == 409
    dec = client.post(f"/advisor/cases/{cid}/decision", json={"approved": True, "decided_refund": 8_000_000}, headers=ADV).json()
    assert dec["status"] == "환급결정"

    prog = client.get(f"/cases/{cid}/progress").json()
    assert prog["fee"]["fee"] == 1_200_000 and prog["fee"]["total"] == 1_320_000
    paid = client.post(f"/cases/{cid}/payment", json={"method": "card"}).json()
    assert paid["status"] == "정산완료"
    assert all(s["state"] == "done" for s in paid["steps"])

    bill = client.get("/advisor/billing", headers=ADV).json()
    mine = [p for p in bill["payments"] if p["case_id"] == cid]
    assert {p["purpose"] for p in mine} == {"success_fee", "platform_fee"}


def test_rejected_claim_has_no_fee():
    cid = _diagnosed_case()
    client.post(f"/cases/{cid}/request", json={"agree_fee_terms": True})
    n = len(client.get(f"/advisor/cases/{cid}", headers=ADV).json()["strategy"]["items"])
    review = {"items": [{"index": i, "decision": "채택" if i == 0 else "제외"} for i in range(n)], "claim_statement": "x"}
    client.put(f"/advisor/cases/{cid}/review", json=review, headers=ADV)
    client.post(f"/advisor/cases/{cid}/file", headers=ADV)
    prog_case = client.post(f"/advisor/cases/{cid}/decision", json={"approved": False}, headers=ADV).json()
    assert prog_case["status"] == "불인용"
    prog = client.get(f"/cases/{cid}/progress").json()
    assert prog["fee"] is None and prog["steps"][4]["state"] == "skip"
    assert client.post(f"/cases/{cid}/payment", json={"method": "card"}).status_code == 409


def _input():
    data = sample_taxpayer()
    return AnalysisInput(report=run_diagnosis(data), data=data, concern="공제", concern_rule_ids=["CGT_LTHD_RESIDENCE"])


def test_mock_analyst_puts_concern_first():
    inp = _input()
    draft = analyze(MockAnalyst(), inp)
    assert [it.priority for it in draft.items] == list(range(1, len(draft.items) + 1))
    concerned = [it for it in draft.items if it.rule_id in inp.concern_rule_ids]
    if concerned:
        assert draft.items[0].rule_id == "CGT_LTHD_RESIDENCE"


def test_strict_schema_closes_every_object():
    def walk(s):
        if isinstance(s, dict):
            if s.get("type") == "object" and "properties" in s:
                assert s["additionalProperties"] is False
                assert set(s["required"]) == set(s["properties"])
            for v in s.values():
                walk(v)
        elif isinstance(s, list):
            for v in s:
                walk(v)
    walk(OUTPUT_SCHEMA)


class _FakeMessages:
    def __init__(self, payload):
        self.payload, self.kwargs = payload, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="end_turn",
            model=kwargs["model"],
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload, ensure_ascii=False))],
        )


def test_claude_analyst_request_and_reconcile():
    inp = _input()
    rule = inp.report.findings[0]
    payload = {
        "customer_summary": "요약",
        "advisor_summary": "세무사 요약",
        "claim_statement": "1. 이유",
        "items": [
            {"rule_id": rule.rule_id, "tax_type": rule.tax_type, "property_id": rule.property_id, "title": "t",
             "priority": 2, "claim_ground": "g", "legal_basis": ["소득세법"], "evidence_on_hand": [],
             "evidence_needed": [], "risks": "r", "customer_questions": [], "estimated_refund": 1,
             "claim_deadline": "2099-01-01"},
            {"rule_id": "MADE_UP", "tax_type": "취득세", "property_id": None, "title": "AI 추가 쟁점",
             "priority": 1, "claim_ground": "g", "legal_basis": [], "evidence_on_hand": [],
             "evidence_needed": [], "risks": "r", "customer_questions": [], "estimated_refund": 999,
             "claim_deadline": None},
        ],
    }
    fake = _FakeMessages(payload)
    analyst = ClaudeAnalyst(client=SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    draft = analyze(analyst, inp)

    assert fake.kwargs["model"] == "claude-opus-5-5"
    assert fake.kwargs["output_config"]["format"]["type"] == "json_schema"
    sent = fake.kwargs["messages"][0]["content"]
    assert "01000000000" not in sent and "홍길동" not in sent  # 연락처·이름은 보내지 않는다

    added, ruled = draft.items
    assert added.rule_id is None and added.estimated_refund is None  # AI가 지어낸 룰 id·금액은 지운다
    assert ruled.claim_deadline == rule.claim_deadline  # 마감일은 룰 계산을 따른다


def test_filing_splits_national_and_local_tax_channels():
    cid = _diagnosed_case()
    client.post(f"/cases/{cid}/request", json={"agree_fee_terms": True})
    items = client.get(f"/advisor/cases/{cid}", headers=ADV).json()["strategy"]["items"]
    taxes = {it["tax_type"] for it in items}
    assert {"양도소득세", "취득세", "종합부동산세"} <= taxes
    review = {"items": [{"index": i, "decision": "채택", "refund": 1_000_000} for i in range(len(items))],
              "claim_statement": "청구 이유"}
    client.put(f"/advisor/cases/{cid}/review", json=review, headers=ADV)
    filed = client.post(f"/advisor/cases/{cid}/file", headers=ADV).json()
    channels = {s["tax_type"]: s["channel"] for s in filed["filing"]["submissions"]}
    assert channels == {"양도소득세": "홈택스", "종합부동산세": "홈택스", "취득세": "위택스"}
    w = next(s for s in filed["filing"]["submissions"] if s["channel"] == "위택스")
    assert w["receipt_no"].startswith("W")


def test_mock_statement_is_grouped_by_tax():
    draft = analyze(MockAnalyst(), _input())
    assert "[취득세 · 위택스]" in draft.claim_statement
    assert "[양도소득세 · 홈택스]" in draft.claim_statement
    acq = next(it for it in draft.items if it.tax_type == "취득세")
    assert any("지방세" in b for b in acq.legal_basis)
    assert "위택스 취득세 신고·납부 내역" in acq.evidence_on_hand
