import base64
import binascii
import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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
_SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


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
    ]
    selected: list[PiiSpan] = []
    for candidate in sorted(candidates, key=lambda span: (-(span.end - span.start), span.start)):
        if all(candidate.end <= span.start or candidate.start >= span.end for span in selected):
            selected.append(candidate)
    selected.sort(key=lambda span: span.start)

    masked = text
    for span in reversed(selected):
        masked = f"{masked[: span.start]}[{span.kind}]{masked[span.end :]}"

    if any(
        pattern.search(masked) for pattern in (*_REJECT_PATTERNS.values(), *_MASK_PATTERNS.values())
    ):
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
