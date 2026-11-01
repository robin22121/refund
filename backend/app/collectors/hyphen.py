"""하이픈 API 수집기.

확인된 것: OAuth 토큰(POST /oauth/token, user_id + hkey, 7일 유효),
[암호화] 필드는 Ekey로 AES128-CBC 후 Base64.

TODO(하이픈 상담 후):
- 홈택스 세금신고내역·양도소득세 상품이 간편인증 로그인을 지원하는지
- 간편인증 2-way 흐름의 엔드포인트와 파라미터, CBC의 IV 규칙
- 상품별 응답을 TaxpayerData로 바꾸는 매핑
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..models import TaxpayerData
from .base import EasyAuthRequest

TOKEN_TTL_SECONDS = 7 * 24 * 3600


def encrypt_field(value: str, ekey: str, iv: bytes | None = None) -> str:
    """하이픈 [암호화] 필드용 AES128-CBC + Base64.

    IV 규칙은 명세서 확인 전이라 기본값은 16바이트 0으로 둔다.
    """
    key = ekey.encode()[:16]
    iv = iv or bytes(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(enc.update(padded) + enc.finalize()).decode()


@dataclass
class HyphenCollector:
    user_id: str
    hkey: str
    ekey: str
    base_url: str = "https://api.hyphen.im"
    _token: str | None = field(default=None, repr=False)
    _token_expires: float = 0.0

    @classmethod
    def from_env(cls) -> HyphenCollector:
        return cls(
            user_id=os.environ["HYPHEN_USER_ID"],
            hkey=os.environ["HYPHEN_HKEY"],
            ekey=os.environ["HYPHEN_EKEY"],
            base_url=os.getenv("HYPHEN_BASE_URL", "https://api.hyphen.im"),
        )

    def token(self) -> str:
        if self._token and time.time() < self._token_expires - 3600:
            return self._token
        res = httpx.post(
            f"{self.base_url}/oauth/token",
            json={"user_id": self.user_id, "hkey": self.hkey},
            timeout=10,
        )
        res.raise_for_status()
        self._token = res.json()["access_token"]
        self._token_expires = time.time() + TOKEN_TTL_SECONDS
        return self._token

    def start_auth(self, req: EasyAuthRequest) -> str:
        raise NotImplementedError("하이픈 간편인증 명세 확인 후 구현")

    def collect(self, session_id: str) -> TaxpayerData:
        raise NotImplementedError("하이픈 상품별 명세 확인 후 구현")
