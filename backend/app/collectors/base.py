"""자료 수집기 인터페이스.

간편인증은 두 단계다. start_auth로 사용자 휴대폰에 인증 요청을 보내고,
사용자가 카카오 등에서 승인하면 collect로 자료를 가져온다.
"""
from datetime import date
from enum import Enum
from typing import Protocol

from pydantic import BaseModel

from ..models import TaxpayerData


class EasyAuthProvider(str, Enum):
    KAKAO = "kakao"
    NAVER = "naver"
    PASS = "pass"
    TOSS = "toss"


class EasyAuthRequest(BaseModel):
    name: str
    birth_date: date
    phone: str
    provider: EasyAuthProvider = EasyAuthProvider.KAKAO


class Collector(Protocol):
    def start_auth(self, req: EasyAuthRequest) -> str:
        """인증 요청을 보내고 세션 id를 돌려준다."""
        ...

    def collect(self, session_id: str) -> TaxpayerData:
        """승인된 세션으로 홈택스·위택스·등기·대장·초본 자료를 모은다."""
        ...
