"""수수료와 결제.

- 진단: 무료
- 성공보수: 고객이 제휴 세무사에게 낸다. 환급 결정액 × 요율 (부가세 별도)
- 플랫폼 이용료: 플랫폼이 세무사에게서 받는다. 경정청구 제출 1건당 고정액

결제는 아직 PG 계약 전이라 MockPaymentGateway로 흉내 낸다.
TODO(이지원): 요율·이용료는 세무사와 계약이 정해지면 바꿔 주세요.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

SUCCESS_FEE_RATE = float(os.getenv("SUCCESS_FEE_RATE", "0.15"))
VAT_RATE = 0.10
PLATFORM_FEE_PER_CASE = int(os.getenv("PLATFORM_FEE_PER_CASE", "50000"))


def fee_terms() -> dict:
    return {
        "success_fee_rate": SUCCESS_FEE_RATE,
        "vat_rate": VAT_RATE,
        "text": [
            "진단과 신청 준비는 무료입니다.",
            f"세무서가 환급을 결정하면 결정된 환급액의 {SUCCESS_FEE_RATE:.0%}(부가세 별도)를 담당 세무사에게 성공보수로 냅니다.",
            "환급이 결정되지 않으면 성공보수는 없습니다.",
            "세무서에 제출하기 전에는 언제든 취소할 수 있고 비용이 없습니다.",
        ],
    }


def success_fee(decided_refund: int) -> dict:
    fee = int(round(decided_refund * SUCCESS_FEE_RATE, -1))
    vat = int(round(fee * VAT_RATE, -1))
    return {"refund": decided_refund, "fee": fee, "vat": vat, "total": fee + vat}


class PaymentMethod(str, Enum):
    CARD = "card"
    TRANSFER = "transfer"  # 계좌이체
    KAKAOPAY = "kakaopay"


class Payment(BaseModel):
    id: str
    case_id: str
    payer: str
    payee: str
    purpose: str = Field(description="success_fee | platform_fee")
    amount: int
    method: str
    approved_at: datetime
    receipt_no: str


class MockPaymentGateway:
    """PG 대신 바로 승인한다. 실제 PG(토스페이먼츠 등)로 바꿀 때 charge만 갈아 끼운다."""

    def charge(self, *, case_id: str, payer: str, payee: str, purpose: str, amount: int, method: str) -> Payment:
        if amount <= 0:
            raise ValueError("결제 금액이 없습니다")
        return Payment(
            id=f"pay_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            payer=payer,
            payee=payee,
            purpose=purpose,
            amount=amount,
            method=method,
            approved_at=datetime.now(timezone.utc),
            receipt_no=f"MOCK-{uuid.uuid4().hex[:8].upper()}",
        )


class Ledger:
    """결제 기록. TODO: DB로 교체."""

    def __init__(self) -> None:
        self.payments: list[Payment] = []

    def add(self, p: Payment) -> Payment:
        self.payments.append(p)
        return p

    def for_case(self, case_id: str) -> list[Payment]:
        return [p for p in self.payments if p.case_id == case_id]
