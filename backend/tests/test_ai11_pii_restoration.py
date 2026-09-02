"""
AI-11(마스킹 placeholder를 실제 값으로 추론·복원 금지) 정식 회귀 테스트.

지금까지 tests/test_manual.py 하나(전화번호 케이스 1개)에만 있던 걸,
전화번호·계좌번호·이메일 복원 차단 / placeholder 자체 허용 / 일반 문자열 허용 /
날짜 필드 예외 / API가 INVALID_SCHEMA로 안전하게 실패 처리하는지까지 포함해서
정식 pytest로 옮긴다.
"""

import pytest
from pydantic import ValidationError

from app.ai import validate_no_restored_pii
from app.codes import FieldStatus
from app.errors import ServiceError
from app.schemas import (
    CandidateField,
    ConsultationCandidate,
    ExtractionResult,
    TechnicalCandidate,
)
from app.security import SensitiveInputError, assert_no_unmasked_pii


def _unknown() -> CandidateField:
    return CandidateField(value=None, status=FieldStatus.UNKNOWN, evidence_quote=None)


def _confirmed(value: str) -> CandidateField:
    return CandidateField(value=value, status=FieldStatus.CONFIRMED_FROM_TEXT, evidence_quote=value)


def _extraction_result(
    *, symptom: str | None = None, symbol_name: str | None = None
) -> ExtractionResult:
    return ExtractionResult(
        schema_version="v1",
        taxonomy_version="v1",
        adapter_name="test",
        model_id=None,
        technical=TechnicalCandidate(
            issue_type=_unknown(),
            symptom=_confirmed(symptom) if symptom is not None else _unknown(),
            submission_status=_unknown(),
            error_code=_unknown(),
            reported_occurred_at=_unknown(),
        ),
        consultation=ConsultationCandidate(
            action=_unknown(),
            symbol_name=_confirmed(symbol_name) if symbol_name is not None else _unknown(),
            symbol_code=_unknown(),
            quantity=_unknown(),
            order_type=_unknown(),
            price_krw=_unknown(),
            attempted_at=_unknown(),
        ),
    )


# --- assert_no_unmasked_pii: 저수준 정규식 함수 ---


@pytest.mark.parametrize(
    "text",
    [
        "010-1234-5678",
        "제 번호는 01012345678 입니다",
        "123-456-7890123",
        "user@example.com",
    ],
)
def test_assert_no_unmasked_pii_blocks_real_pii(text: str) -> None:
    with pytest.raises(SensitiveInputError):
        assert_no_unmasked_pii(text)


@pytest.mark.parametrize("text", ["[PHONE]", "[ACCOUNT]", "[EMAIL]"])
def test_assert_no_unmasked_pii_allows_placeholders(text: str) -> None:
    assert_no_unmasked_pii(text)  # 예외 없이 통과해야 정상


@pytest.mark.parametrize(
    "text",
    [
        "로딩이 멈춤",
        "로그인이 되지 않음",
        "주문 화면이 멈춤",
        "",
    ],
)
def test_assert_no_unmasked_pii_allows_plain_text(text: str) -> None:
    assert_no_unmasked_pii(text)


# --- validate_no_restored_pii: ExtractionResult 전체 검증 ---


def test_validate_no_restored_pii_blocks_phone_in_symptom() -> None:
    result = _extraction_result(symptom="010-1234-5678")
    with pytest.raises(SensitiveInputError):
        validate_no_restored_pii(result)


def test_validate_no_restored_pii_blocks_account_in_symbol_name() -> None:
    result = _extraction_result(symbol_name="123-456-7890123")
    with pytest.raises(SensitiveInputError):
        validate_no_restored_pii(result)


def test_validate_no_restored_pii_blocks_email() -> None:
    result = _extraction_result(symptom="user@example.com")
    with pytest.raises(SensitiveInputError):
        validate_no_restored_pii(result)


def test_validate_no_restored_pii_allows_placeholder_value() -> None:
    result = _extraction_result(symptom="[PHONE]")
    validate_no_restored_pii(result)  # 예외 없이 통과해야 정상


def test_validate_no_restored_pii_allows_plain_symptom() -> None:
    result = _extraction_result(symptom="로그인이 되지 않음")
    validate_no_restored_pii(result)


def test_validate_no_restored_pii_ignores_iso_datetime_fields() -> None:
    """FE-07 정상 추출(날짜+시각)이 계좌번호 정규식과 우연히 겹쳐 오탐나던 회귀 버그."""
    result = ExtractionResult(
        schema_version="v1",
        taxonomy_version="v1",
        adapter_name="test",
        model_id=None,
        technical=TechnicalCandidate(
            issue_type=_unknown(),
            symptom=_unknown(),
            submission_status=_unknown(),
            error_code=_unknown(),
            reported_occurred_at=CandidateField(
                value="2026-08-15T11:00:00+09:00",
                status=FieldStatus.CONFIRMED_FROM_TEXT,
                evidence_quote="2026년 8월 15일 오전 11시에",
            ),
        ),
        consultation=ConsultationCandidate(
            action=_unknown(),
            symbol_name=_unknown(),
            symbol_code=_unknown(),
            quantity=_unknown(),
            order_type=_unknown(),
            price_krw=_unknown(),
            attempted_at=_unknown(),
        ),
    )
    validate_no_restored_pii(
        result
    )  # 예외 없이 통과해야 정상 (2026-08-15가 계좌번호로 오탐되면 안 됨)


# --- API 계층: INVALID_SCHEMA로 안전하게 실패 처리되는지 ---


def test_sensitive_input_error_is_a_value_error() -> None:
    """analyze_report()의 except (ValidationError, ValueError) 분기가 SensitiveInputError도
    잡아서 INVALID_SCHEMA로 처리할 수 있는지 타입 관계로 확인한다 (DB 없이 확인 가능한 부분)."""
    assert issubclass(SensitiveInputError, ValueError)
    try:
        assert_no_unmasked_pii("010-1234-5678")
    except Exception as exc:
        assert isinstance(exc, ValidationError | ValueError)
    else:
        pytest.fail("SensitiveInputError가 발생해야 합니다")


def test_service_error_import_available_for_invalid_report_path() -> None:
    # reports.py의 INVALID_REPORT 경로가 사용하는 예외 타입이 여전히 존재하는지만 확인.
    assert ServiceError is not None
