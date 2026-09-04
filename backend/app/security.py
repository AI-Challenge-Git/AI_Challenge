import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError


class PiiDecision(StrEnum):
    ALLOW = "ALLOW"
    MASKED = "MASKED"
    REJECT = "REJECT"


class InvalidReportTextError(ValueError):
    pass


class SensitiveInputError(ValueError):
    pass


class InvalidSessionTokenError(ValueError):
    pass


@dataclass(frozen=True)
class PiiSpan:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class PiiScanResult:
    decision: PiiDecision
    masked_text: str
    detected_kinds: tuple[str, ...]
    spans: tuple[PiiSpan, ...]


_REJECT_PATTERNS = {
    "RESIDENT_REGISTRATION_NUMBER": re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)"),
    "OTP": re.compile(r"(?:OTP|일회용\s*비밀번호)\s*[:：은는]?\s*\d{4,8}", re.IGNORECASE),
    "PASSWORD": re.compile(
        r"(?:비밀번호|패스워드)\s*[:：은는]?\s*[A-Za-z0-9!@#$%^&*]{4,30}",
        re.IGNORECASE,
    ),
}
_MASK_PATTERNS = {
    "PHONE": re.compile(
        r"(?<!\d)(?:(?:01[016789]|0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70|80))"
        r"\s*[-.) ]?\s*\d{3,4}\s*[-. ]?\s*\d{4}|1[5-8]\d{2}\s*[-. ]?\s*\d{4})(?!\d)"
    ),
    "EMAIL": re.compile(
        r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9._%+-])",
        re.IGNORECASE,
    ),
    "ACCOUNT": re.compile(r"(?<!\d)(?:\d{10,14}|\d{2,6}\s*[- ]\s*\d{2,6}\s*[- ]\s*\d{2,8})(?!\d)"),
}
_PLACEHOLDER_ALIASES = {
    "[전화번호]": "[PHONE]",
    "[계좌번호]": "[ACCOUNT]",
    "[이메일]": "[EMAIL]",
}
# ACCOUNT 마스킹 패턴(숫자-숫자-숫자)이 "26-09-29" 같은 대시형 날짜와 우연히
# 겹쳐서, AI가 보기도 전에 원문에서 날짜가 통째로 사라지는 실제 오탐이
# 있었다(월/일을 0으로 채운 경우만 걸림 - "26-9-29"는 안 걸리고
# "26-09-29"는 걸림). 대시로 구분된 3구간이 유효한 달력 날짜(월 1-12,
# 일 1-31)로 해석되면 계좌번호로 마스킹하지 않는다. 실제 계좌번호도
# 우연히 이 범위에 들 수는 있지만, 그 경우 PHONE/EMAIL과 달리 계좌번호는
# 애초에 자유서술에 등장할 근거 자체가 약해 트레이드오프로 받아들인다.
_DASH_DATE_PATTERN = re.compile(r"(?<!\d)(\d{2,4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})(?!\d)")


def _is_plausible_calendar_date(year: str, month: str, day: str) -> bool:
    year_int = int(year)
    if len(year) == 2:
        year_int += 2000
    return 2000 <= year_int <= 2099 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31


def _is_date_like_account_match(matched_text: str) -> bool:
    date_match = _DASH_DATE_PATTERN.fullmatch(matched_text)
    return date_match is not None and _is_plausible_calendar_date(*date_match.groups())


def _has_unexcused_pii(text: str) -> bool:
    """마스킹 대상 PII가 남아있는지 확인한다. 날짜로 보이는 ACCOUNT 매칭은 제외한다.

    scan_and_mask()의 후보 선정과 재검증, assert_no_unmasked_pii() 셋 다
    이 함수를 써야 한다 - 마스킹 단계에서 날짜라서 안 가린 텍스트를
    재검증 단계에서 다시 "PII가 남아있다"고 걸러버리면 자기모순이 된다.
    """
    if any(pattern.search(text) for pattern in _REJECT_PATTERNS.values()):
        return True
    for kind, pattern in _MASK_PATTERNS.items():
        for match in pattern.finditer(text):
            if kind == "ACCOUNT" and _is_date_like_account_match(match.group()):
                continue
            return True
    return False


_SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash(secrets.token_urlsafe(32))


def assert_no_unmasked_pii(text: str) -> None:
    """AI-11: masked_text에서 마스킹했던 PII 패턴이 AI 응답 값에 재등장하면 거부한다."""
    if _has_unexcused_pii(text):
        raise SensitiveInputError("AI 응답 값에 마스킹 대상 PII 패턴이 재등장했습니다")


def normalize_placeholders(text: str) -> str:
    for localized, canonical in _PLACEHOLDER_ALIASES.items():
        text = text.replace(localized, canonical)
    return text


def normalize_report_text(text: str) -> str:
    normalized = normalize_placeholders(unicodedata.normalize("NFC", text.strip()))
    if not 20 <= len(normalized) <= 500:
        raise InvalidReportTextError("report text must contain 20 to 500 Unicode code points")
    return normalized


def scan_and_mask(text: str) -> PiiScanResult:
    rejected = tuple(kind for kind, pattern in _REJECT_PATTERNS.items() if pattern.search(text))
    if rejected:
        return PiiScanResult(PiiDecision.REJECT, "", rejected, ())

    candidates = [
        PiiSpan(match.start(), match.end(), kind)
        for kind, pattern in _MASK_PATTERNS.items()
        for match in pattern.finditer(text)
        if not (kind == "ACCOUNT" and _is_date_like_account_match(match.group()))
    ]
    selected: list[PiiSpan] = []
    for candidate in sorted(candidates, key=lambda span: (-(span.end - span.start), span.start)):
        if all(candidate.end <= span.start or candidate.start >= span.end for span in selected):
            selected.append(candidate)
    selected.sort(key=lambda span: span.start)

    masked = text
    for span in reversed(selected):
        masked = f"{masked[: span.start]}[{span.kind}]{masked[span.end :]}"

    if _has_unexcused_pii(masked):
        raise SensitiveInputError("sensitive data remained after masking")

    kinds = tuple(dict.fromkeys(span.kind for span in selected))
    decision = PiiDecision.MASKED if selected else PiiDecision.ALLOW
    return PiiScanResult(decision, masked, kinds, tuple(selected))


def ensure_confirmation_strings_are_safe(*values: str | None) -> None:
    for value in values:
        if value is None:
            continue
        result = scan_and_mask(value)
        if result.decision is not PiiDecision.ALLOW:
            raise SensitiveInputError("confirmation fields cannot contain sensitive data")


def decode_session_token(token: str) -> bytes:
    if not _SESSION_TOKEN_PATTERN.fullmatch(token):
        raise InvalidSessionTokenError("invalid session token")
    try:
        decoded = base64.urlsafe_b64decode(f"{token}=")
    except (ValueError, binascii.Error) as exc:
        raise InvalidSessionTokenError("invalid session token") from exc
    if len(decoded) != 32:
        raise InvalidSessionTokenError("invalid session token")
    return decoded


def session_digest(token: str, hmac_key: bytes) -> bytes:
    if len(hmac_key) < 32:
        raise ValueError("session HMAC key must contain at least 32 bytes")
    return hmac.digest(hmac_key, decode_session_token(token), "sha256")


def make_reference_number(
    principal_digest: bytes,
    analysis_id: bytes,
    client_request_id: bytes,
    hmac_key: bytes,
) -> str:
    if len(hmac_key) < 32:
        raise ValueError("reference HMAC key must contain at least 32 bytes")
    entropy = hmac.digest(hmac_key, principal_digest + analysis_id + client_request_id, "sha256")[
        :16
    ]
    encoded = base64.b32encode(entropy).decode().rstrip("=")
    return f"KBSOS-{encoded}"


def reference_digest(reference_number: str, hmac_key: bytes) -> bytes:
    if len(hmac_key) < 32:
        raise ValueError("reference HMAC key must contain at least 32 bytes")
    return hmac.digest(hmac_key, reference_number.encode("ascii"), "sha256")


def canonical_json_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def hash_password(password: str) -> str:
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _DUMMY_PASSWORD_HASH
    try:
        return _PASSWORD_HASH.verify(password, candidate_hash) and password_hash is not None
    except UnknownHashError:
        return False


def make_opaque_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def opaque_token_digest(token: str, hmac_key: bytes) -> bytes:
    if len(hmac_key) < 32:
        raise ValueError("agent token HMAC key must contain at least 32 bytes")
    return hmac.digest(hmac_key, decode_session_token(token), "sha256")


def keyed_fingerprint(value: str, namespace: str, hmac_key: bytes) -> bytes:
    if len(hmac_key) < 32:
        raise ValueError("rate limit HMAC key must contain at least 32 bytes")
    normalized = unicodedata.normalize("NFC", value.strip())
    return hmac.digest(hmac_key, f"{namespace}\0{normalized}".encode(), "sha256")
