"""세무사 검토와 경정청구 대행 기록."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ItemDecision(str, Enum):
    ADOPT = "채택"
    HOLD = "보류"   # 고객 증빙을 기다리는 중
    DROP = "제외"


class ItemReview(BaseModel):
    """AI 전략 항목 하나에 대한 세무사 판단. index는 strategy.items의 위치."""

    index: int
    decision: ItemDecision = ItemDecision.HOLD
    refund: int | None = Field(None, description="세무사가 계산한 청구 환급액(원)")
    note: str = ""


class Review(BaseModel):
    items: list[ItemReview] = []
    claim_statement: str = Field("", description="세무사가 고친 청구 이유")
    advisor_memo: str = Field("", description="세무사 내부 메모. 고객에게 보이지 않음")
    customer_message: str = Field("", description="고객에게 보낼 안내·자료 요청")
    reviewer: str = ""
    reviewed_at: datetime | None = None


class ReviewUpdate(BaseModel):
    items: list[ItemReview]
    claim_statement: str
    advisor_memo: str = ""
    customer_message: str = ""


class Submission(BaseModel):
    """세목별 경정청구서 한 건. 국세는 홈택스, 지방세는 위택스로 낸다."""

    tax_type: str
    channel: str
    receipt_no: str
    claimed_refund: int
    items: list[int] = Field(description="이 청구서에 들어간 strategy.items 위치")


class Filing(BaseModel):
    filed_at: datetime
    submissions: list[Submission]
    claimed_refund: int
    result_due: date = Field(description="결과 통지 기한 (청구일부터 2개월)")


class DecisionInput(BaseModel):
    approved: bool
    decided_refund: int = 0
    decided_on: date | None = None
    note: str = ""


class Decision(DecisionInput):
    recorded_at: datetime


class ClaimRequest(BaseModel):
    agree_fee_terms: bool
    concern_rule_ids: list[str] = []


class PaymentRequest(BaseModel):
    method: str = "card"
