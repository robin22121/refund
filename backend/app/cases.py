"""고객 접수 흐름.

1. 양도자산 소재지 입력 → 등기부·대장·토지이용계획 자동 조회
2. 검토 희망 항목(가장 세금이 아까운 부분) 입력
3. 전자위임장 서명
4. 홈택스 간편인증 → 자료 수집 → 진단

순서를 건너뛸 수 없도록 상태로 막는다.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from .analysis.models import StrategyDraft
from .claims import Decision, Filing, Review
from .collectors.base import EasyAuthProvider
from .models import DiagnosisReport, Property, TaxpayerData
from .security import Cipher, sha256_hex


class CaseStatus(str, Enum):
    RECEIVED = "접수"            # 소재지·검토 희망 항목 입력 완료
    MANDATE_SIGNED = "위임완료"   # 전자위임장 서명 완료
    AUTHENTICATING = "인증중"     # 홈택스 간편인증 요청됨
    DIAGNOSED = "진단완료"
    REQUESTED = "신청접수"       # 고객이 수수료 약정에 동의하고 세무사에게 맡김. AI 초안 생성
    REVIEWING = "세무사검토"      # 세무사가 초안을 검토·수정하는 중
    FILED = "청구제출"           # 경정청구서 제출, 세무서 결과 대기
    DECIDED = "환급결정"         # 세무서가 환급을 결정, 성공보수 결제 대기
    REJECTED = "불인용"          # 세무서가 받아들이지 않음, 비용 없음
    SETTLED = "정산완료"         # 성공보수 결제 완료


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
    documents: list[str] = Field([], description="자동으로 발급·조회한 서류")
    error: str | None = None


class CaseCreate(BaseModel):
    name: str
    birth_date: date
    phone: str
    addresses: list[str] = Field(min_length=1, description="양도한 부동산 소재지")
    concern: str = Field("", description="가장 세금이 아깝다고 느끼는 부분, 검토 희망 항목")


class ConcernUpdate(BaseModel):
    concern: str
    concern_rule_ids: list[str] = Field([], description="고객이 고른 항목과 연결된 룰 id. AI가 우선 검토")


# 전자위임장을 받는 제휴 세무사. TODO: 제휴 세무사 목록이 정해지면 DB로 옮긴다.
MANDATE_AGENT = {
    "office": "한빛세무회계 (예시)",
    "name": "김한빛 세무사",
    "registration_no": "제12345호",
}


SIGNATURE_MAX_CHARS = 400_000  # 서명 이미지 data URL 최대 길이 (약 300KB)
SIGNATURE_PREFIXES = ("data:image/png;base64,", "data:image/jpeg;base64,")


class MandateSign(BaseModel):
    signer_name: str
    agreed_scopes: list[str]
    signature_image: str = Field(description="서명 이미지 (PNG·JPEG data URL)", max_length=SIGNATURE_MAX_CHARS)

    @field_validator("signature_image")
    @classmethod
    def _image(cls, v: str) -> str:
        if not v.startswith(SIGNATURE_PREFIXES) or len(v) <= len(SIGNATURE_PREFIXES[0]) + 8:
            raise ValueError("서명 이미지가 올바르지 않습니다")
        return v


class Mandate(BaseModel):
    """전자위임장. 무엇을 누구에게 위임했는지와 서명을 해시로 묶어 나중에 바뀌지 않았음을 보인다."""

    signer_name: str
    agent: dict = Field(description="수임 세무사")
    scopes: list[str]
    signature_image: str = Field(description="API 응답에서는 빠진다")
    signed_at: datetime
    signer_ip: str = ""
    document_hash: str = Field("", description="위임 내용 + 서명 이미지의 SHA-256")


def mandate_hash(m: Mandate) -> str:
    doc = {
        "signer_name": m.signer_name,
        "agent": m.agent,
        "scopes": m.scopes,
        "signed_at": m.signed_at.isoformat(),
        "signature_sha256": sha256_hex(m.signature_image),
    }
    return sha256_hex(json.dumps(doc, ensure_ascii=False, sort_keys=True))


class AuthStart(BaseModel):
    provider: EasyAuthProvider = EasyAuthProvider.KAKAO


class Case(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    applicant: CaseCreate
    access_token_hash: str | None = Field(None, description="고객 접근 토큰 해시. API 응답에서는 빠진다")
    assets: list[AssetLookup] = []
    status: CaseStatus = CaseStatus.RECEIVED
    mandate: Mandate | None = None
    auth_session_id: str | None = None
    report: DiagnosisReport | None = None
    data: TaxpayerData | None = Field(None, description="수집한 원자료. AI 분석과 세무사 검토용")
    concern_rule_ids: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requested_at: datetime | None = None
    fee_agreed_at: datetime | None = None
    strategy: StrategyDraft | None = None
    strategy_error: str | None = None
    review: Review | None = None
    filing: Filing | None = None
    decision: Decision | None = None


# API 응답에서 뺄 필드. 고객은 자기 건이라도 원자료·세무사 검토 내용은 보지 않는다.
INTERNAL_FIELDS = {"access_token_hash": True, "auth_session_id": True, "mandate": {"signature_image"}}
CUSTOMER_HIDDEN = INTERNAL_FIELDS | {"data": True, "strategy": True, "review": True, "strategy_error": True}


def customer_view(case: Case) -> dict:
    return case.model_dump(mode="json", exclude=CUSTOMER_HIDDEN)


def advisor_view(case: Case) -> dict:
    return case.model_dump(mode="json", exclude=INTERNAL_FIELDS)


class CaseStore:
    """임시 메모리 저장소. 건마다 AES-256-GCM으로 암호화해 둔다 (건 id를 AAD로 묶어 바꿔치기 방지).

    TODO: DB로 교체. 그때도 암호문 컬럼에 그대로 넣고, 검색이 필요한 값만 따로 해시 컬럼을 둔다.
    """

    def __init__(self, cipher: Cipher | None = None) -> None:
        self._cipher = cipher or Cipher.from_env()
        self._cases: dict[str, str] = {}

    def save(self, case: Case) -> Case:
        self._cases[case.id] = self._cipher.seal(case.model_dump_json(), aad=case.id)
        return case

    def get(self, case_id: str) -> Case | None:
        blob = self._cases.get(case_id)
        return None if blob is None else Case.model_validate_json(self._cipher.open(blob, aad=case_id))

    def all(self) -> list[Case]:
        return [c for cid in self._cases if (c := self.get(cid))]

    def raw(self, case_id: str) -> str | None:
        """저장된 암호문 (테스트·점검용)."""
        return self._cases.get(case_id)


def sign_mandate(case: Case, req: MandateSign, signer_ip: str = "") -> Case:
    missing = [s for s in MANDATE_SCOPES if s not in req.agreed_scopes]
    if missing:
        raise ValueError(f"동의하지 않은 위임 항목: {', '.join(missing)}")
    if req.signer_name != case.applicant.name:
        raise ValueError("서명자 이름이 신청인과 다릅니다")
    if case.status not in (CaseStatus.RECEIVED, CaseStatus.MANDATE_SIGNED):
        raise ValueError("인증을 시작한 뒤에는 위임장을 다시 쓸 수 없습니다")
    case.mandate = Mandate(
        signer_name=req.signer_name,
        agent=dict(MANDATE_AGENT),
        scopes=list(MANDATE_SCOPES),
        signature_image=req.signature_image,
        signed_at=datetime.now(timezone.utc),
        signer_ip=signer_ip,
    )
    case.mandate.document_hash = mandate_hash(case.mandate)
    case.status = CaseStatus.MANDATE_SIGNED
    return case
