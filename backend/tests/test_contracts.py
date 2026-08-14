import base64
import unicodedata
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.ai import FakeDualExtractor
from app.codes import OrderAction, OrderType, SubmissionStatus
from app.schemas import ConsultationConfirmation, ReportCreateRequest, TechnicalConfirmation
from app.security import (
    InvalidReportTextError,
    InvalidSessionTokenError,
    PiiDecision,
    SensitiveInputError,
    canonical_json_sha256,
    decode_session_token,
    ensure_confirmation_strings_are_safe,
    normalize_report_text,
    scan_and_mask,
    session_digest,
)


@pytest.mark.parametrize("length", [20, 500])
def test_report_text_accepts_unicode_boundaries(length: int) -> None:
    text = "가" * length
    assert normalize_report_text(text) == text


@pytest.mark.parametrize("length", [19, 501])
def test_report_text_rejects_outside_unicode_boundaries(length: int) -> None:
    with pytest.raises(InvalidReportTextError):
        normalize_report_text("🙂" * length)


def test_report_text_is_trimmed_and_nfc_normalized() -> None:
    decomposed = "e\u0301" * 20
    normalized = normalize_report_text(f"  {decomposed}  ")

    assert normalized == unicodedata.normalize("NFC", decomposed)
    assert len(normalized) == 20


def test_pii_filter_masks_allowed_types_without_returning_values() -> None:
    text = "주문 오류입니다. 전화 010-1234-5678, 메일 test@example.com으로 연락했습니다."
    result = scan_and_mask(text)

    assert result.decision is PiiDecision.MASKED
    assert "010-1234-5678" not in result.masked_text
    assert "test@example.com" not in result.masked_text
    assert "[PHONE]" in result.masked_text
    assert "[EMAIL]" in result.masked_text
    assert set(result.detected_kinds) == {"PHONE", "EMAIL"}


@pytest.mark.parametrize(
    "text",
    [
        "주민등록번호는 900101-1234567입니다.",
        "비밀번호는 secret1234입니다.",
        "OTP는 123456입니다.",
    ],
)
def test_pii_filter_rejects_authentication_and_resident_values(text: str) -> None:
    result = scan_and_mask(text)
    assert result.decision is PiiDecision.REJECT
    assert result.masked_text == ""


def test_confirmation_strings_reject_reintroduced_sensitive_values() -> None:
    with pytest.raises(SensitiveInputError):
        ensure_confirmation_strings_are_safe("삼성전자", "010-1234-5678")


def test_session_token_requires_32_random_bytes_and_uses_hmac() -> None:
    raw = bytes(range(32))
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    assert len(token) == 43
    assert decode_session_token(token) == raw
    assert session_digest(token, b"s" * 32) != session_digest(token, b"r" * 32)
    with pytest.raises(InvalidSessionTokenError):
        decode_session_token("short")


def test_confirmation_schema_rejects_invalid_financial_values() -> None:
    with pytest.raises(ValidationError):
        ConsultationConfirmation(
            action=OrderAction.SELL,
            symbol_name="삼성전자",
            symbol_code="5930",
            quantity=-1,
            order_type=OrderType.LIMIT,
            price_krw=0,
            attempted_at=None,
        )
    with pytest.raises(ValidationError):
        ConsultationConfirmation(
            action=OrderAction.SELL,
            symbol_name="삼성전자",
            symbol_code="005930",
            quantity=1,
            order_type=OrderType.MARKET,
            price_krw=70_000,
            attempted_at=None,
        )


def test_confirmation_schema_requires_timezone_aware_times() -> None:
    with pytest.raises(ValidationError):
        TechnicalConfirmation(
            issue_type="UNKNOWN",
            symptom=None,
            submission_status=SubmissionStatus.UNKNOWN,
            error_code=None,
            reported_occurred_at=datetime(2026, 8, 14, 9, 3),
        )


def test_fake_extractor_is_deterministic_and_contains_no_order_data_in_technical() -> None:
    extractor = FakeDualExtractor()
    first = extractor.extract("주문 버튼을 누른 뒤 계속 로딩되고 결과를 확인하지 못했습니다.")
    second = extractor.extract("주문 버튼을 누른 뒤 계속 로딩되고 결과를 확인하지 못했습니다.")

    assert first == second
    assert first.adapter_name == "fake"
    assert first.technical.issue_type.value is None
    assert {"symbol_name", "symbol_code", "quantity", "price_krw", "action"}.isdisjoint(
        type(first.technical).model_fields
    )


def test_contracts_forbid_unknown_fields_and_canonical_hash_is_stable() -> None:
    with pytest.raises(ValidationError):
        ReportCreateRequest.model_validate(
            {
                "client_request_id": "58e06f0a-1220-46a0-b30f-e840716846be",
                "text": "주문 버튼을 누른 뒤 계속 로딩되고 결과를 확인하지 못했습니다.",
                "raw_text": "must not be accepted",
            }
        )

    assert canonical_json_sha256({"b": 2, "a": "한글"}) == canonical_json_sha256(
        {"a": "한글", "b": 2}
    )
