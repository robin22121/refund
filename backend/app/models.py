"""납세자 자료와 진단 결과를 담는 도메인 모델."""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class PropertyKind(str, Enum):
    HOUSE = "주택"
    LAND = "토지"
    BUILDING = "건물"


class AcquisitionCause(str, Enum):
    PURCHASE = "매매"
    INHERITANCE = "상속"
    GIFT = "증여"
    NEW_BUILD = "신축"


class Property(BaseModel):
    """등기부·대장·토지이용계획에서 모은 부동산 정보."""

    id: str
    kind: PropertyKind
    address: str
    pnu: str | None = Field(None, description="필지고유번호. 대장·토지이용계획 조회 키")
    land_category: str | None = Field(None, description="지목 (전, 답, 임야, 대 등)")
    zoning: str | None = Field(None, description="용도지역")
    acquired_on: date
    acquisition_cause: AcquisitionCause = AcquisitionCause.PURCHASE
    registered_price: int | None = Field(None, description="등기부 거래가액(원)")
    building_approved_on: date | None = Field(None, description="건축물대장 사용승인일")
    disposed_on: date | None = None


class Residence(BaseModel):
    """주민등록초본의 전입 이력 한 줄."""

    address: str
    moved_in: date
    moved_out: date | None = None
    property_id: str | None = Field(None, description="해당 주소가 보유 부동산이면 그 id")


class CapitalGainsReturn(BaseModel):
    """홈택스 양도소득세 신고 내역."""

    property_id: str
    transfer_date: date
    transfer_price: int
    acquisition_price: int
    used_converted_price: bool = Field(False, description="환산취득가액 적용 여부")
    necessary_expenses: int = 0
    long_term_deduction_rate: float = Field(0.0, description="적용된 장기보유특별공제율 (0.0~0.8)")
    exemption_applied: bool = Field(False, description="1세대 1주택 비과세 적용 여부")
    non_business_land_surcharge: bool = Field(False, description="비사업용토지 중과 적용 여부")
    tax_paid: int = 0


class LocalTaxPayment(BaseModel):
    """위택스 지방세 신고·납부 내역."""

    tax_type: str = Field(description="취득세, 재산세 등")
    property_id: str | None = None
    amount: int
    paid_on: date


class TaxpayerData(BaseModel):
    """수집기가 돌려주는 한 사람의 자료 묶음."""

    name: str
    birth_date: date
    properties: list[Property] = []
    residences: list[Residence] = []
    capital_gains_returns: list[CapitalGainsReturn] = []
    local_tax_payments: list[LocalTaxPayment] = []


class Confidence(str, Enum):
    HIGH = "높음"
    MEDIUM = "보통"
    NEEDS_REVIEW = "세무사 검토 필요"


class Finding(BaseModel):
    """룰 하나가 찾아낸 환급 가능성."""

    rule_id: str
    tax_type: str
    title: str
    reason: str
    confidence: Confidence
    estimated_refund: int | None = Field(None, description="추정 환급액(원). 계산이 어려우면 None")
    required_documents: list[str] = []
    claim_deadline: date | None = Field(None, description="경정청구 마감일")
    property_id: str | None = None


class DiagnosisReport(BaseModel):
    taxpayer_name: str
    findings: list[Finding]
