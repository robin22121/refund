"""개인정보 보호와 접근 통제.

- 저장 암호화: 접수 건(Case) 전체를 AES-256-GCM으로 암호화해 저장한다. 이름·연락처·생년월일·
  수집 자료·서명 이미지, 앞으로 받을 주민등록번호까지 저장소에는 암호문만 남는다.
  키는 DATA_ENCRYPTION_KEY(Base64 32바이트). 운영에서는 KMS에서 받아 넣는다.
- 고객 접근 토큰: 접수할 때 한 번만 내려 주고 해시만 저장한다. 고객 API는 X-Case-Token 헤더로 확인한다.
- 세무사 접근 키: 운영 환경에서는 기본값을 쓸 수 없고, 연속 실패하면 잠근다.
- 접속 기록: 개인정보 처리 화면 접근을 남긴다 (개인정보의 안전성 확보조치 기준 제8조).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("refund.security")
audit_log = logging.getLogger("refund.audit")

DEMO_ADVISOR_KEY = "demo-advisor"


def is_production() -> bool:
    return os.getenv("APP_ENV", "dev").lower() in ("prod", "production")


class ConfigError(RuntimeError):
    pass


# ---------- 저장 암호화 ----------

class Cipher:
    """AES-256-GCM. 암호문 형식: 'v1:' + Base64(nonce 12바이트 + 암호문 + 태그)."""

    PREFIX = "v1:"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ConfigError("DATA_ENCRYPTION_KEY는 Base64로 인코딩한 32바이트여야 합니다")
        self._aead = AESGCM(key)

    @classmethod
    def from_env(cls) -> Cipher:
        raw = os.getenv("DATA_ENCRYPTION_KEY")
        if raw:
            return cls(base64.b64decode(raw))
        if is_production():
            raise ConfigError("운영 환경에는 DATA_ENCRYPTION_KEY가 필요합니다")
        log.warning("DATA_ENCRYPTION_KEY가 없어 임시 키를 만듭니다. 재시작하면 저장 자료를 읽을 수 없습니다.")
        return cls(AESGCM.generate_key(bit_length=256))

    def seal(self, plaintext: str, aad: str = "") -> str:
        nonce = secrets.token_bytes(12)
        ct = self._aead.encrypt(nonce, plaintext.encode(), aad.encode() or None)
        return self.PREFIX + base64.b64encode(nonce + ct).decode()

    def open(self, token: str, aad: str = "") -> str:
        if not token.startswith(self.PREFIX):
            raise ValueError("알 수 없는 암호문 형식")
        blob = base64.b64decode(token[len(self.PREFIX):])
        return self._aead.decrypt(blob[:12], blob[12:], aad.encode() or None).decode()


# ---------- 주민등록번호 ----------

RRN_RE = re.compile(r"^(\d{6})-?([1-8])(\d{6})$")


def normalize_rrn(value: str) -> str:
    """형식만 확인한다. 2020.10. 이후 번호는 검증번호 규칙이 없어 체크섬은 보지 않는다."""
    m = RRN_RE.match(value.strip())
    if not m:
        raise ValueError("주민등록번호 형식이 아닙니다")
    return f"{m[1]}-{m[2]}{m[3]}"


def mask_rrn(value: str) -> str:
    """화면 표시용. 생년월일과 성별 자리만 보인다 (650302-1******)."""
    rrn = normalize_rrn(value)
    return rrn[:8] + "*" * 6


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[:3] + "-****-" + digits[-4:] if len(digits) >= 10 else "****"


# ---------- 토큰 ----------

def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, digest: str | None) -> bool:
    return bool(token and digest) and secrets.compare_digest(hash_token(token), digest)


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# ---------- 세무사 접근 키 ----------

def advisor_key_from_env() -> str:
    key = os.getenv("ADVISOR_KEY", DEMO_ADVISOR_KEY)
    if is_production() and (key == DEMO_ADVISOR_KEY or len(key) < 32):
        raise ConfigError("운영 환경에서는 32자 이상의 ADVISOR_KEY를 지정해야 합니다")
    return key


@dataclass
class FailureLimiter:
    """접근 키를 연속으로 틀리면 잠시 막는다 (IP 기준, 메모리)."""

    max_failures: int = 10
    window_seconds: int = 600
    _fails: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def _trim(self, who: str, now: float) -> deque:
        q = self._fails[who]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        return q

    def blocked(self, who: str) -> bool:
        return len(self._trim(who, time.monotonic())) >= self.max_failures

    def fail(self, who: str) -> None:
        self._trim(who, time.monotonic()).append(time.monotonic())

    def reset(self, who: str) -> None:
        self._fails.pop(who, None)


# ---------- 접속 기록 ----------

@dataclass
class AuditEntry:
    at: datetime
    actor: str
    action: str
    case_id: str | None
    ip: str


class AuditLog:
    """개인정보 접근 기록. TODO: DB·별도 저장소에 1년 이상(5만 명 이상이면 2년) 보관."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, actor: str, action: str, case_id: str | None, ip: str) -> None:
        e = AuditEntry(datetime.now(timezone.utc), actor, action, case_id, ip)
        self.entries.append(e)
        audit_log.info("%s %s case=%s ip=%s", actor, action, case_id, ip)


# ---------- 응답 헤더 ----------

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}
