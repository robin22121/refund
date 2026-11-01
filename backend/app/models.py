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
    """위택스 지방세 신고·납부 내역. 취득세는 신고서의 과세표준·세율·감면까지 담는다."""

    tax_type: str = Field(description="취득세, 재산세 등")
    property_id: str | None = None
    amount: int = Field(description="납부한 본세(원). 지방교육세·농어촌특별세는 뺀다")
    paid_on: date
    tax_base: int | None = Field(None, description="과세표준(취득당시가액, 원)")
    rate: float | None = Field(None, description="적용 세율 (0.01 = 1%)")
    reduction: int = Field(0, description="감면받은 세액(원)")
    reduction_name: str | None = Field(None, description="적용된 감면 이름. 예: 생애최초 주택 구입")


class ComprehensiveTaxReturn(BaseModel):
    """홈택스 종합부동산세(주택분) 고지·신고 내역 한 해 치."""

    year: int = Field(description="귀속연도. 과세기준일은 그해 6월 1일")
    houses_counted: int = Field(description="세액 계산에 쓴 주택 수")
    one_house_applied: bool = Field(False, description="1세대 1주택자 계산(기본공제·세액공제) 적용 여부")
    basic_deduction: int = Field(description="적용된 공제금액(원)")
    heavy_rate_applied: bool = Field(False, description="다주택 중과세율 적용 여부")
    age_credit_rate: float = Field(0.0, description="적용된 고령자 세액공제율")
    holding_credit_rate: float = Field(0.0, description="적용된 장기보유 세액공제율")
    tax_paid: int = Field(description="납부한 종부세 본세(원). 농어촌특별세는 뺀다")
    filed_by_return: bool = Field(False, description="고지 대신 신고납부했는지")


class TaxpayerData(BaseModel):
    """수집기가 돌려주는 한 사람의 자료 묶음."""

    name: str
    birth_date: date
    properties: list[Property] = []
    residences: list[Residence] = []
    capital_gains_returns: list[CapitalGainsReturn] = []
    local_tax_payments: list[LocalTaxPayment] = []
    comprehensive_tax_returns: list[ComprehensiveTaxReturn] = []


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
    property_address: str | None = Field(None, description="화면 표시용 부동산 소재지")


class DiagnosisReport(BaseModel):
    taxpayer_name: str
    findings: list[Finding]
    concern: str = Field("", description="고객이 적은 검토 희망 항목. AI 분석과 세무사 검토의 출발점")
