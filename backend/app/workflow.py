"""진단 이후 흐름: 신청 → AI 초안 → 세무사 검토 → 경정청구 제출 → 결과 → 성공보수 정산.

각 함수는 상태를 확인하고 틀린 순서면 WorkflowError를 낸다.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from .analysis import AnalysisError, AnalysisInput, Analyst, analyze
from .billing import PLATFORM_FEE_PER_CASE, Ledger, MockPaymentGateway, success_fee
from .cases import MANDATE_AGENT, Case, CaseStatus
from .claims import (
    ClaimRequest,
    Decision,
    DecisionInput,
    Filing,
    ItemDecision,
    ItemReview,
    Review,
    ReviewUpdate,
    Submission,
)

from .analysis.legal import CHANNEL  # 국세는 홈택스, 지방세는 위택스로 경정청구한다

PLATFORM = "플랫폼"
RESULT_DAYS = 61  # 국세기본법 제45조의2 제3항: 청구일부터 2개월 안에 통지


class WorkflowError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _need(case: Case, *allowed: CaseStatus) -> None:
    if case.status not in allowed:
        raise WorkflowError(f"지금 단계({case.status.value})에서는 할 수 없습니다")


def generate_strategy(case: Case, analyst: Analyst) -> Case:
    """AI 초안을 만든다. 실패해도 신청은 접수된 채로 두고 세무사가 직접 검토하게 한다."""
    inp = AnalysisInput(
        report=case.report,
        data=case.data,
        assets=case.assets,
        concern=case.applicant.concern,
        concern_rule_ids=case.concern_rule_ids,
    )
    try:
        case.strategy = analyze(analyst, inp)
        case.strategy_error = None
    except (AnalysisError, ValueError) as e:
        case.strategy_error = str(e)
    except Exception as e:  # API 장애 등. 고객 신청은 막지 않는다
        case.strategy_error = f"AI 분석 중 오류가 났습니다: {type(e).__name__}"
    if case.strategy:
        case.review = Review(
            items=[ItemReview(index=i, refund=it.estimated_refund) for i, it in enumerate(case.strategy.items)],
            claim_statement=case.strategy.claim_statement,
        )
    return case


def request_claim(case: Case, req: ClaimRequest, analyst: Analyst) -> Case:
    _need(case, CaseStatus.DIAGNOSED)
    if not req.agree_fee_terms:
        raise WorkflowError("수수료 약정에 동의해야 신청할 수 있습니다", 422)
    if req.concern_rule_ids:
        case.concern_rule_ids = req.concern_rule_ids
    case.fee_agreed_at = case.requested_at = _now()
    case.status = CaseStatus.REQUESTED
    return generate_strategy(case, analyst)


def save_review(case: Case, upd: ReviewUpdate) -> Case:
    _need(case, CaseStatus.REQUESTED, CaseStatus.REVIEWING)
    n = len(case.strategy.items) if case.strategy else 0
    if any(not 0 <= it.index < n for it in upd.items):
        raise WorkflowError("없는 전략 항목입니다", 422)
    case.review = Review(
        **upd.model_dump(), reviewer=MANDATE_AGENT["name"], reviewed_at=_now()
    )
    case.status = CaseStatus.REVIEWING
    return case


def file_claim(case: Case, ledger: Ledger, gateway: MockPaymentGateway) -> Case:
    """채택한 항목을 세목별 청구서로 묶어 제출한다(가짜). 제출 건마다 플랫폼 이용료를 세무사에게 청구한다."""
    _need(case, CaseStatus.REVIEWING)
    adopted = [r for r in case.review.items if r.decision == ItemDecision.ADOPT]
    if not adopted:
        raise WorkflowError("채택한 항목이 없습니다", 422)
    if any(r.decision == ItemDecision.HOLD for r in case.review.items):
        raise WorkflowError("보류 중인 항목을 채택이나 제외로 정해 주세요", 422)
    if not case.review.claim_statement.strip():
        raise WorkflowError("청구 이유를 적어 주세요", 422)
    today = date.today()
    expired = [
        case.strategy.items[r.index].title
        for r in adopted
        if (d := case.strategy.items[r.index].claim_deadline) and d < today
    ]
    if expired:
        raise WorkflowError(f"경정청구 기한이 지난 항목: {', '.join(expired)}", 422)

    by_tax: dict[str, list[ItemReview]] = {}
    for r in adopted:
        by_tax.setdefault(case.strategy.items[r.index].tax_type, []).append(r)
    submissions = [
        Submission(
            tax_type=tax,
            channel=CHANNEL.get(tax, "홈택스"),
            receipt_no=f"{'H' if CHANNEL.get(tax, '홈택스') == '홈택스' else 'W'}{today:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
            claimed_refund=sum(r.refund or 0 for r in rs),
            items=[r.index for r in rs],
        )
        for tax, rs in by_tax.items()
    ]
    case.filing = Filing(
        filed_at=_now(),
        submissions=submissions,
        claimed_refund=sum(s.claimed_refund for s in submissions),
        result_due=today + timedelta(days=RESULT_DAYS),
    )
    ledger.add(
        gateway.charge(
            case_id=case.id,
            payer=MANDATE_AGENT["office"],
            payee=PLATFORM,
            purpose="platform_fee",
            amount=PLATFORM_FEE_PER_CASE,
            method="invoice",
        )
    )
    case.status = CaseStatus.FILED
    return case


def record_decision(case: Case, req: DecisionInput) -> Case:
    _need(case, CaseStatus.FILED)
    if req.approved and req.decided_refund <= 0:
        raise WorkflowError("환급 결정액을 적어 주세요", 422)
    case.decision = Decision(
        **req.model_dump() | {"decided_refund": req.decided_refund if req.approved else 0},
        recorded_at=_now(),
    )
    case.status = CaseStatus.DECIDED if req.approved else CaseStatus.REJECTED
    return case


def pay_success_fee(case: Case, method: str, ledger: Ledger, gateway: MockPaymentGateway) -> Case:
    _need(case, CaseStatus.DECIDED)
    fee = success_fee(case.decision.decided_refund)
    ledger.add(
        gateway.charge(
            case_id=case.id,
            payer=case.applicant.name,
            payee=MANDATE_AGENT["office"],
            purpose="success_fee",
            amount=fee["total"],
            method=method,
        )
    )
    case.status = CaseStatus.SETTLED
    return case


# ---------- 고객 화면용 진행 상황 ----------

# 상태별로 지금 진행 중인 단계 위치 (그 앞 단계는 끝난 것)
_NOW_STEP = {
    CaseStatus.REQUESTED: 1,
    CaseStatus.REVIEWING: 1,
    CaseStatus.FILED: 3,
    CaseStatus.DECIDED: 4,
    CaseStatus.REJECTED: 4,
    CaseStatus.SETTLED: 5,
}


def progress(case: Case, ledger: Ledger) -> dict:
    """고객에게 보여 줄 진행 상황. 세무사 내부 메모와 AI 세무사용 요약은 뺀다."""
    st = case.status
    pos = _NOW_STEP.get(st, 0)
    f, d = case.filing, case.decision
    steps = [
        {"key": "requested", "label": "신청 접수", "at": case.requested_at, "detail": "세무사에게 맡기셨어요"},
        {"key": "reviewing", "label": "세무사 확인", "at": case.review.reviewed_at if case.review else None,
         "detail": "서류와 신고 내용을 다시 확인해요 (1~2주)"},
        {"key": "filed", "label": "세무서에 환급 신청", "at": f.filed_at if f else None,
         "detail": (", ".join(f"{s.channel} 접수번호 {s.receipt_no}" for s in f.submissions) if f else "세무사가 경정청구서를 대신 냅니다")},
        {"key": "decided", "label": "세무서 결과 통지",
         "at": d.recorded_at if d else None,
         "detail": (f"{f.result_due:%Y년 %m월 %d일}까지 결과가 나와요" if f and not d else
                    ("환급이 결정됐어요" if d and d.approved else ("이번에는 받아들여지지 않았어요" if d else "신청한 날부터 2개월 안에")))},
        {"key": "settled", "label": "환급금 입금 · 수수료 정산", "at": None, "detail": "환급금은 고객님 계좌로 바로 들어가요"},
    ]
    for i, s in enumerate(steps):
        s["state"] = "done" if i < pos else ("now" if i == pos else "todo")
    if st == CaseStatus.REJECTED:
        steps[4]["state"] = "skip"
        steps[4]["detail"] = "환급이 없으니 수수료도 없어요"
    paid = [p for p in ledger.for_case(case.id) if p.purpose == "success_fee"]
    if paid:
        steps[4]["at"] = paid[-1].approved_at
    adopted = []
    if case.strategy and case.review:
        for r in case.review.items:
            if r.decision == ItemDecision.ADOPT:
                it = case.strategy.items[r.index]
                adopted.append({"title": it.title, "tax_type": it.tax_type, "refund": r.refund})
    return {
        "status": st,
        "steps": steps,
        "summary": case.strategy.customer_summary if case.strategy else None,
        "message": case.review.customer_message if case.review else "",
        "questions": [q for it in (case.strategy.items if case.strategy else []) for q in it.customer_questions]
        if st == CaseStatus.REQUESTED else [],
        "adopted": adopted if st not in (CaseStatus.REQUESTED, CaseStatus.REVIEWING) else [],
        "claimed_refund": f.claimed_refund if f else None,
        "decision": d,
        "fee": success_fee(d.decided_refund) if d and d.approved else None,
        "payment": paid[-1] if paid else None,
    }
