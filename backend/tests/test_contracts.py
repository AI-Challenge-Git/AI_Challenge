import base64
import unicodedata
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.ai import FakeDualExtractor
from app.codes import FieldStatus, IssueType, OrderAction, OrderType, SubmissionStatus
from app.schemas import (
    CandidateField,
    ConsultationConfirmation,
    ReportCreateRequest,
    TechnicalConfirmation,
    _evidence_contains_explicit_date_and_time,
)
from app.security import (
    InvalidReportTextError,
    InvalidSessionTokenError,
    PiiDecision,
    SensitiveInputError,
    canonical_json_sha256,
    decode_session_token,
    ensure_confirmation_strings_are_safe,
    make_reference_number,
    normalize_report_text,
    reference_digest,
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


def test_localized_placeholders_are_normalized_to_canonical_values() -> None:
    normalized = normalize_report_text(
        "주문 오류 제보이며 [전화번호], [계좌번호], [이메일]은 사용자가 가렸습니다."
    )

    assert normalized == ("주문 오류 제보이며 [PHONE], [ACCOUNT], [EMAIL]은 사용자가 가렸습니다.")
    assert "[전화번호]" not in normalized
    assert "[계좌번호]" not in normalized
    assert "[이메일]" not in normalized


def test_api_and_ai_dtos_canonicalize_placeholder_aliases() -> None:
    request = ReportCreateRequest.model_validate(
        {
            "client_request_id": "58e06f0a-1220-46a0-b30f-e840716846be",
            "text": "주문 오류 제보이며 [전화번호]는 직접 가린 합성 값입니다.",
        }
    )
    candidate = CandidateField[str](
        value="[계좌번호]가 표시된 화면",
        status=FieldStatus.CONFIRMED_FROM_TEXT,
        evidence_quote="[계좌번호]",
    )

    assert request.text == "주문 오류 제보이며 [PHONE]는 직접 가린 합성 값입니다."
    assert candidate.value == "[ACCOUNT]가 표시된 화면"
    assert candidate.evidence_quote == "[ACCOUNT]"


def test_ai_candidate_placeholder_normalization_preserves_str_enum_values() -> None:
    issue_type = CandidateField[IssueType](
        value=IssueType.ORDER_SUBMISSION_FAILURE,
        status=FieldStatus.CONFIRMED_FROM_TEXT,
        evidence_quote="주문 화면이 멈췄습니다.",
    )
    submission_status = CandidateField[SubmissionStatus](
        value=SubmissionStatus.CUSTOMER_REPORTED_SUBMITTED,
        status=FieldStatus.CONFIRMED_FROM_TEXT,
        evidence_quote="주문을 제출했습니다.",
    )

    assert issue_type.value is IssueType.ORDER_SUBMISSION_FAILURE
    assert submission_status.value is SubmissionStatus.CUSTOMER_REPORTED_SUBMITTED


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
    ("text", "sensitive_value", "kind"),
    [
        ("대표번호는 1588-1234입니다.", "1588-1234", "PHONE"),
        ("인터넷 전화는 070-1234-5678입니다.", "070-1234-5678", "PHONE"),
        ("붙여 쓴 휴대전화는 01012345678입니다.", "01012345678", "PHONE"),
        ("계좌 후보는 1234567890입니다.", "1234567890", "ACCOUNT"),
        ("계좌 후보는 12345678901234입니다.", "12345678901234", "ACCOUNT"),
        ("계좌 후보는 123-456-789012입니다.", "123-456-789012", "ACCOUNT"),
    ],
)
def test_pii_filter_masks_supported_synthetic_formats(
    text: str,
    sensitive_value: str,
    kind: str,
) -> None:
    result = scan_and_mask(text)

    assert result.decision is PiiDecision.MASKED
    assert sensitive_value not in result.masked_text
    assert result.detected_kinds == (kind,)


@pytest.mark.parametrize("digits", ["1" * 9, "1" * 15])
def test_compact_account_candidate_has_explicit_length_boundaries(digits: str) -> None:
    result = scan_and_mask(f"합성 숫자 {digits}입니다.")
    assert result.decision is PiiDecision.ALLOW


@pytest.mark.parametrize(
    "text",
    [
        "26-09-29 오전 11시 23분 쯤에 매도 주문을 넣었는데 접수가 안 됩니다.",
        "2026-09-29 오전 11시에 매수 주문을 넣었습니다.",
        "26-9-29 8시 23분에 주문했다.",
    ],
)
def test_dash_dates_are_not_masked_as_account(text: str) -> None:
    """계좌번호 정규식(숫자-숫자-숫자)이 대시형 날짜와 겹쳐서 AI가 보기 전에
    날짜가 통째로 사라지던 회귀. 월(1-12)·일(1-31)이 유효한 대시형 날짜는
    계좌번호로 마스킹하면 안 된다."""
    result = scan_and_mask(text)
    assert result.decision is PiiDecision.ALLOW
    assert result.masked_text == text


@pytest.mark.parametrize(
    ("text", "sensitive_value"),
    [
        ("13-45-67 이체해주세요.", "13-45-67"),
        ("99-99-99로 송금 부탁드립니다.", "99-99-99"),
    ],
)
def test_invalid_calendar_dates_still_mask_as_account(text: str, sensitive_value: str) -> None:
    """월/일 범위를 벗어나 날짜로 해석될 수 없는 숫자-대시 패턴은 계좌번호
    마스킹이 그대로 적용돼야 한다 (날짜 예외가 계좌번호 탐지를 무력화하면 안 됨)."""
    result = scan_and_mask(text)
    assert result.decision is PiiDecision.MASKED
    assert sensitive_value not in result.masked_text
    assert result.detected_kinds == ("ACCOUNT",)


@pytest.mark.parametrize(
    "evidence",
    [
        "26/07/18 23시 34분에",
        "2026/07/18 23:34에",
        "26-9-29 8시 23분",
        "2026년 8월 15일 오전 9시 3분에",
    ],
)
def test_evidence_with_valid_date_and_time_is_recognized(evidence: str) -> None:
    """FE-07: 날짜·시각이 모두 있는 근거는 표기 형식(슬래시/콜론/한국어)과
    무관하게 완전한 것으로 인식돼야 한다."""
    assert _evidence_contains_explicit_date_and_time(evidence) is True


@pytest.mark.parametrize(
    "evidence",
    [
        "23:89",  # 89분은 존재하지 않는 시각
        "26/07/18",  # 날짜만 있고 시각 없음
        "23시 34분",  # 시각만 있고 날짜 없음
    ],
)
def test_evidence_missing_date_or_time_is_not_recognized(evidence: str) -> None:
    assert _evidence_contains_explicit_date_and_time(evidence) is False


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


def test_reference_number_has_128_bit_output_and_is_never_the_stored_value() -> None:
    principal = b"p" * 32
    reference = make_reference_number(principal, b"a" * 16, b"r" * 16, b"k" * 32)

    assert len(reference) == 32
    assert reference.startswith("KBSOS-")
    assert reference == make_reference_number(principal, b"a" * 16, b"r" * 16, b"k" * 32)
    assert reference.encode() != reference_digest(reference, b"k" * 32)
    assert reference_digest(reference, b"k" * 32) != reference_digest(reference, b"q" * 32)


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
            order_type=OrderType.LIMIT,
            price_krw=None,
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


@pytest.mark.parametrize("action", list(OrderAction))
def test_confirmation_schema_accepts_all_order_actions(action: OrderAction) -> None:
    confirmation = ConsultationConfirmation(
        action=action,
        symbol_name=None,
        symbol_code=None,
        quantity=None,
        order_type=OrderType.UNKNOWN,
        price_krw=None,
        attempted_at=None,
    )

    assert confirmation.action is action


def test_confirmation_schema_accepts_uppercase_alphanumeric_symbol_code() -> None:
    confirmation = ConsultationConfirmation(
        action=OrderAction.BUY,
        symbol_name="액스비스",
        symbol_code="0011A0",
        quantity=1,
        order_type=OrderType.MARKET,
        price_krw=None,
        attempted_at=None,
    )

    assert confirmation.symbol_code == "0011A0"
    with pytest.raises(ValidationError):
        ConsultationConfirmation(
            action=OrderAction.BUY,
            symbol_name="액스비스",
            symbol_code="0011a0",
            quantity=1,
            order_type=OrderType.MARKET,
            price_krw=None,
            attempted_at=None,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("quantity", True),
        ("quantity", "20"),
        ("price_krw", True),
        ("price_krw", "10000"),
    ],
)
def test_confirmation_schema_rejects_coerced_numbers(field_name: str, value: object) -> None:
    payload: dict[str, object] = {
        "action": "SELL",
        "symbol_name": "합성종목",
        "symbol_code": "000000",
        "quantity": 20,
        "order_type": "LIMIT",
        "price_krw": 10_000,
        "attempted_at": None,
    }
    payload[field_name] = value

    with pytest.raises(ValidationError):
        ConsultationConfirmation.model_validate(payload)


def test_confirmation_schema_trims_symptom_and_rejects_blank_value() -> None:
    confirmation = TechnicalConfirmation(
        issue_type=IssueType.UNKNOWN,
        symptom="  화면 멈춤  ",
        submission_status=SubmissionStatus.UNKNOWN,
        error_code=None,
        reported_occurred_at=None,
    )
    assert confirmation.symptom == "화면 멈춤"

    with pytest.raises(ValidationError):
        TechnicalConfirmation(
            issue_type=IssueType.UNKNOWN,
            symptom="   ",
            submission_status=SubmissionStatus.UNKNOWN,
            error_code=None,
            reported_occurred_at=None,
        )


def test_confirmation_schema_requires_timezone_aware_times() -> None:
    with pytest.raises(ValidationError):
        TechnicalConfirmation(
            issue_type=IssueType.UNKNOWN,
            symptom=None,
            submission_status=SubmissionStatus.UNKNOWN,
            error_code=None,
            reported_occurred_at=datetime(2026, 8, 14, 9, 3),
        )


async def test_fake_extractor_is_deterministic_and_contains_no_order_data_in_technical() -> None:
    extractor = FakeDualExtractor()
    first = await extractor.extract("주문 버튼을 누른 뒤 계속 로딩되고 결과를 확인하지 못했습니다.")
    second = await extractor.extract(
        "주문 버튼을 누른 뒤 계속 로딩되고 결과를 확인하지 못했습니다."
    )

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


def test_report_create_requires_uuid4() -> None:
    accepted = ReportCreateRequest.model_validate(
        {
            "client_request_id": "58e06f0a-1220-46a0-b30f-e840716846be",
            "text": "합성 제보 문장으로 실제 개인정보를 포함하지 않습니다.",
        }
    )
    assert accepted.client_request_id.version == 4

    with pytest.raises(ValidationError):
        ReportCreateRequest.model_validate(
            {
                "client_request_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "text": "합성 제보 문장으로 실제 개인정보를 포함하지 않습니다.",
            }
        )
