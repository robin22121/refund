from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..models import DiagnosisReport, TaxpayerData
from .models import StrategyDraft


class AnalysisInput(BaseModel):
    """AI에 넘기는 한 고객의 자료. 연락처 같은 분석에 필요 없는 개인정보는 넣지 않는다."""

    report: DiagnosisReport
    data: TaxpayerData | None = None
    assets: list = []  # cases.AssetLookup (순환 import를 피하려고 느슨하게 둔다)
    concern: str = ""
    concern_rule_ids: list[str] = []


class Analyst(Protocol):
    model: str

    def draft(self, inp: AnalysisInput) -> StrategyDraft: ...


def reconcile(draft: StrategyDraft, inp: AnalysisInput) -> StrategyDraft:
    """AI 초안을 룰 결과와 맞춘다.

    마감일은 룰 엔진 계산을 따르고, 룰이 금액을 계산한 항목은 룰 금액을 쓴다.
    AI가 새로 제기한 쟁점은 금액을 비워 세무사가 직접 계산하게 한다.
    """
    by_rule = {(f.rule_id, f.property_id): f for f in inp.report.findings}
    rule_ids = {f.rule_id for f in inp.report.findings}
    for it in draft.items:
        f = by_rule.get((it.rule_id, it.property_id))
        if f is None and it.rule_id in rule_ids:
            f = next(x for x in inp.report.findings if x.rule_id == it.rule_id)
        if f is not None:
            it.claim_deadline = f.claim_deadline
            if f.estimated_refund is not None:
                it.estimated_refund = f.estimated_refund
        else:
            it.rule_id = None
            it.estimated_refund = None
    draft.items.sort(key=lambda it: it.priority)
    for i, it in enumerate(draft.items, start=1):
        it.priority = i
    return draft
