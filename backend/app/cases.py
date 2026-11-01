"""고객 접수 흐름.

1. 양도자산 소재지 입력 → 등기부·대장·토지이용계획 자동 조회
2. 검토 희망 항목(가장 세금이 아까운 부분) 입력
3. 전자위임장 서명
4. 홈택스 간편인증 → 자료 수집 → 진단

순서를 건너뛸 수 없도록 상태로 막는다.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from .collectors.base import EasyAuthProvider
from .models import DiagnosisReport, Property


class CaseStatus(str, Enum):
    RECEIVED = "접수"            # 소재지·검토 희망 항목 입력 완료
    MANDATE_SIGNED = "위임완료"   # 전자위임장 서명 완료
    AUTHENTICATING = "인증중"     # 홈택스 간편인증 요청됨
    DIAGNOSED = "진단완료"


MANDATE_SCOPES = [
    "홈택스·위택스 신고·납부 내역 조회",
    "등기부등본·건축물대장·토지이용계획·주민등록초본 발급",
    "양도소득세·취득세·종합부동산세 경정청구 대리",
    "개인정보(고유식별정보 포함) 수집·이용 및 제휴 세무사 제공",
]


class AssetLookup(BaseModel):
    """소재지 하나에 대한 자동 조회 결과."""

    address: str
    property: Property | None = None
    owner_name_matches: bool | None = Field(None, description="등기부 소유자명과 신청인 이름 일치 여부")
    error: str | None = None


class CaseCreate(BaseModel):
    name: str
    birth_date: date
    phone: str
    addresses: list[str] = Field(min_length=1, description="양도한 부동산 소재지")
    concern: str = Field("", description="가장 세금이 아깝다고 느끼는 부분, 검토 희망 항목")


class MandateSign(BaseModel):
    signer_name: str
    agreed_scopes: list[str]
    signature_image: str = Field(description="서명 이미지 (data URL 또는 Base64)")


class Mandate(BaseModel):
    signer_name: str
    scopes: list[str]
    signature_image: str
    signed_at: datetime


class AuthStart(BaseModel):
    provider: EasyAuthProvider = EasyAuthProvider.KAKAO


class Case(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    applicant: CaseCreate
    assets: list[AssetLookup] = []
    status: CaseStatus = CaseStatus.RECEIVED
    mandate: Mandate | None = None
    auth_session_id: str | None = None
    report: DiagnosisReport | None = None


class CaseStore:
    """임시 메모리 저장소. TODO: DB로 교체."""

    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}

    def save(self, case: Case) -> Case:
        self._cases[case.id] = case
        return case

    def get(self, case_id: str) -> Case | None:
        return self._cases.get(case_id)


def sign_mandate(case: Case, req: MandateSign) -> Case:
    missing = [s for s in MANDATE_SCOPES if s not in req.agreed_scopes]
    if missing:
        raise ValueError(f"동의하지 않은 위임 항목: {', '.join(missing)}")
    if req.signer_name != case.applicant.name:
        raise ValueError("서명자 이름이 신청인과 다릅니다")
    case.mandate = Mandate(
        signer_name=req.signer_name,
        scopes=list(MANDATE_SCOPES),
        signature_image=req.signature_image,
        signed_at=datetime.now(timezone.utc),
    )
    case.status = CaseStatus.MANDATE_SIGNED
    return case
