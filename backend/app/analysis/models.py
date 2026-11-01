"""AI 환급 전략 초안 모델.

AI가 쓰는 부분(StrategyOutput)과 저장할 때 붙이는 정보(StrategyDraft)를 나눈다.
StrategyOutput은 Claude 구조화 출력 스키마로 그대로 쓰이므로 필드 설명이 곧 지시문이다.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class StrategyItem(BaseModel):
    """환급 쟁점 하나에 대한 청구 전략."""

    rule_id: str | None = Field(
        None, description="룰 엔진 진단 항목의 rule_id. 룰이 못 찾은 쟁점을 AI가 새로 제기하면 null"
    )
    tax_type: str = Field(description="양도소득세, 취득세, 종합부동산세 중 하나")
    property_id: str | None = Field(None, description="대상 부동산 id")
    title: str = Field(description="쟁점 이름, 20자 안팎")
    priority: int = Field(description="청구 우선순위. 1이 가장 먼저. 환급 가능성·금액·기한을 함께 고려")
    claim_ground: str = Field(description="경정청구 이유. 사실관계와 주장 논리를 세무사가 읽을 문장으로")
    legal_basis: list[str] = Field(description="근거 법령·예규·판례. 예: 소득세법 제89조 제1항 제3호")
    evidence_on_hand: list[str] = Field(description="이미 수집한 자료 중 주장을 뒷받침하는 것")
    evidence_needed: list[str] = Field(description="고객에게 더 받아야 할 증빙")
    risks: str = Field(description="과세관청이 들 수 있는 반론과 불인용 위험")
    customer_questions: list[str] = Field(description="사실관계를 확정하려고 고객에게 물을 질문. 쉬운 말로")
    estimated_refund: int | None = Field(None, description="추정 환급액(원). 근거 없이 지어내지 말고 모르면 null")
    claim_deadline: date | None = Field(None, description="경정청구 마감일")


class StrategyOutput(BaseModel):
    customer_summary: str = Field(description="고객(고령층)에게 보여 줄 3~4문장 요약. 전문용어 없이 존댓말로")
    advisor_summary: str = Field(description="세무사용 요약. 쟁점 순서, 핵심 사실관계, 먼저 확인할 것")
    items: list[StrategyItem]
    claim_statement: str = Field(description="경정청구서에 붙일 '청구 이유' 초안. 쟁점별 번호를 매긴 공문체")


class StrategyDraft(StrategyOutput):
    model: str = Field(description="초안을 쓴 모델. 가짜 분석기는 'mock'")
    generated_at: datetime
