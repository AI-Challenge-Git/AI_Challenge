"""
taxonomy v2(주문 거부 세부 라벨 6개)와 하이픈/점/슬래시 날짜 파싱이
실제 OpenAI 응답에서 회귀 없이 동작하는지 확인하는 라이브 테스트.

check_taxonomy_v2_labels.py / check_date3.py / check_focused_classifier_rejected.py를
CI에서 추적 가능한 pytest로 옮긴 것. 실제 API를 호출해 비용이 발생하므로
tests/test_signals_postgres.py의 RUN_POSTGRES_TESTS 패턴과 동일하게
기본 CI에서는 skip되고, RUN_LIVE_LLM_TESTS=1일 때만 실행된다.
"""

import os

import pytest

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="requires a live OpenAI API call (billed)",
)

# (case_id, 입력 문장, 기대 canonical symptom 문구)
REJECTED_LABEL_CASES = [
    (
        "ORDER_REJECTED_INSUFFICIENT_BALANCE",
        "삼성전자 매수 주문을 넣었는데, 매수가능금액이 부족하다며 주문이 거부됐습니다.",
        "잔고 부족으로 주문이 거부됨",
    ),
    (
        "ORDER_REJECTED_PRICE_LIMIT",
        "지정가를 상한가보다 높게 넣었더니 가격 범위를 벗어났다며 주문이 거부됐습니다.",
        "가격이 제한 범위를 벗어나 주문이 거부됨",
    ),
    (
        "ORDER_REJECTED_QUANTITY_INVALID",
        "1주 미만으로 매도 주문을 넣었더니 최소 주문 수량 조건에 안 맞는다며 거부됐습니다.",
        "수량 또는 단위 오류로 주문이 거부됨",
    ),
    (
        "ORDER_REJECTED_SERVER_ERROR",
        "매수 주문을 넣었는데 서버 통신 오류라는 메시지와 함께 주문이 거부됐습니다.",
        "서버·네트워크 오류로 주문이 거부됨",
    ),
    (
        "ORDER_REJECTED_UNKNOWN_REASON",
        "매도 주문을 넣었는데 이유 설명 없이 그냥 주문이 거부됐다고만 나옵니다.",
        "원인을 알 수 없는 오류로 주문이 거부됨",
    ),
    (
        "APP_TERMINATED_DURING_SUBMISSION",
        "매수 확정 버튼을 누르는 순간 앱이 그대로 꺼져버렸습니다.",
        "주문 중 앱이 강제 종료됨",
    ),
]

# (case_id, 입력 문장, 기대 ISO value)
HYPHEN_DATE_CASES = [
    (
        "hyphen_2digit_year",
        "26-9-29 8시 23분 쯤에 매수 주문을 넣었는데 접수됐는지 확인이 안 됩니다.",
        "2026-09-29T08:23:00+09:00",
    ),
    (
        "dot_2digit_year",
        "26.9.29 오전 8시 23분에 매수 주문을 넣었는데 접수됐는지 확인이 안 됩니다.",
        "2026-09-29T08:23:00+09:00",
    ),
    (
        "slash_date_with_korean_time",
        "26/07/18 23시 34분에 매도 하려고 했는데 최소 주문 수량이 안 맞는다고 거래가 안되네",
        "2026-07-18T23:34:00+09:00",
    ),
    (
        "slash_date_with_colon_time",
        "2026/07/18 23:34에 매도 주문을 넣었는데 최소 수량 오류로 거래가 안 됐어요",
        "2026-07-18T23:34:00+09:00",
    ),
]


@pytest.fixture(scope="module")
def extractor() -> RealDualExtractor:
    return RealDualExtractor()


@pytest.mark.parametrize(
    ("case_id", "text", "expected_symptom"),
    REJECTED_LABEL_CASES,
    ids=[c[0] for c in REJECTED_LABEL_CASES],
)
def test_order_submission_failure_sublabel(
    extractor: RealDualExtractor, case_id: str, text: str, expected_symptom: str
) -> None:
    outcome = extractor.extract_safe(text)
    assert outcome.result is not None, f"{case_id}: 추출 실패 ({outcome.failure_reason})"
    issue_type = outcome.result.technical.issue_type
    symptom = outcome.result.technical.symptom
    assert issue_type.value is IssueType.ORDER_SUBMISSION_FAILURE, (
        f"{case_id}: issue_type={issue_type.value} (기대: ORDER_SUBMISSION_FAILURE)"
    )
    assert symptom.value == expected_symptom, f"{case_id}: symptom={symptom.value!r}"


@pytest.mark.parametrize(
    ("case_id", "text", "expected_symptom"),
    REJECTED_LABEL_CASES,
    ids=[c[0] for c in REJECTED_LABEL_CASES],
)
def test_focused_classifier_matches_order_submission_failure(
    extractor: RealDualExtractor, case_id: str, text: str, expected_symptom: str
) -> None:
    """보조 재분류 프롬프트가 REJECTED/앱종료 문장을 잘못 재분류하지 않는지 직접 확인.

    _classify_issue_type_focused는 메인 추출과 로컬 키워드 후보가 충돌할 때만
    호출되는 보조 경로라, 파이프라인 전체로는 이 경로를 타는지 보장할 수
    없어서 직접 호출한다. "수량 오류 거부" 케이스가 UNRELATED_OR_AMBIGUOUS로
    잘못 분류됐던 실제 회귀가 있었다.
    """
    result = extractor._classify_issue_type_focused(text)
    assert result is not None, f"{case_id}: 보조 분류기가 None 반환"
    predicted, _evidence = result
    assert predicted is IssueType.ORDER_SUBMISSION_FAILURE, (
        f"{case_id}: 보조 분류기 결과={predicted}"
    )


@pytest.mark.parametrize(
    ("case_id", "text", "expected_value"), HYPHEN_DATE_CASES, ids=[c[0] for c in HYPHEN_DATE_CASES]
)
def test_numeric_date_with_2digit_year_confirms(
    extractor: RealDualExtractor, case_id: str, text: str, expected_value: str
) -> None:
    outcome = extractor.extract_safe(text)
    assert outcome.result is not None, f"{case_id}: 추출 실패 ({outcome.failure_reason})"
    field = outcome.result.technical.reported_occurred_at
    assert field.status.value == "CONFIRMED_FROM_TEXT", (
        f"{case_id}: status={field.status} (2자리 연도 확장 실패 가능성)"
    )
    assert field.value == expected_value, f"{case_id}: value={field.value!r}"
