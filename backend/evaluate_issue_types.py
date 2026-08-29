"""실제 LLM의 issue_type 분류 baseline을 측정한다."""

from collections import Counter, defaultdict

from app.codes import FieldStatus, IssueType
from app.real_extractor_v5 import RealDualExtractor


# case_id, expected_issue_type, report_text
CASES = [
    (
        "submission_01",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "주문 버튼을 눌렀는데 화면이 멈췄습니다.",
    ),
    (
        "submission_02",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "매도 확인 후 다음 단계로 넘어가지 않습니다.",
    ),
    (
        "submission_03",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "주문 제출 중 계속 로딩됩니다.",
    ),
    (
        "result_01",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문했는데 접수됐는지 모르겠습니다.",
    ),
    (
        "result_02",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문번호가 표시되지 않아 주문 결과를 확인할 수 없습니다.",
    ),
    (
        "result_03",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "매도 후 체결 여부를 확인할 수 없습니다.",
    ),
    (
        "login_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "비밀번호 오류로 로그인되지 않습니다.",
    ),
    (
        "login_02",
        IssueType.LOGIN_ACCESS_FAILURE,
        "인증번호가 오지 않아 로그인할 수 없습니다.",
    ),
    (
        "login_03",
        IssueType.LOGIN_ACCESS_FAILURE,
        "로그인 버튼을 눌러도 접속되지 않습니다.",
    ),
    (
        "balance_01",
        IssueType.BALANCE_INQUIRY_ERROR,
        "잔고 화면이 갱신되지 않습니다.",
    ),
    (
        "balance_02",
        IssueType.BALANCE_INQUIRY_ERROR,
        "보유 주식 수량이 잔고에 표시되지 않습니다.",
    ),
    (
        "balance_03",
        IssueType.BALANCE_INQUIRY_ERROR,
        "체결 내역을 조회하면 빈 화면이 나옵니다.",
    ),
    (
        "network_01",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "와이파이가 끊길 때 앱이 멈춥니다.",
    ),
    (
        "network_02",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "모바일 데이터로 접속할 때만 앱이 열리지 않습니다.",
    ),
    (
        "network_03",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "다른 휴대전화에서는 되는데 제 기기에서만 실행되지 않습니다.",
    ),
    (
        "unrelated_01",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "해외주식 거래 수수료가 궁금합니다.",
    ),
    (
        "unrelated_02",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "공모주 청약 일정을 알려주세요.",
    ),
    (
        "unrelated_03",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "오늘 주가 전망이 궁금합니다.",
    ),
    (
        "unknown_01",
        IssueType.UNKNOWN,
        "M-able에서 문제가 발생했습니다.",
    ),
    (
        "unknown_02",
        IssueType.UNKNOWN,
        "기능이 제대로 되지 않습니다.",
    ),
    (
        "unknown_03",
        IssueType.UNKNOWN,
        "알 수 없는 오류가 나타납니다.",
    ),
]


def normalized_issue_type(value, status):
    status_value = status.value if hasattr(status, "value") else str(status)
    if value is None and status_value == FieldStatus.UNKNOWN.value:
        return IssueType.UNKNOWN.value
    if value is None:
        return "<NONE>"
    return value.value if hasattr(value, "value") else str(value)


def main():
    extractor = RealDualExtractor()
    confusion = defaultdict(Counter)
    correct_by_type = Counter()
    total_by_type = Counter()
    misclassified = []
    total_attempts = 0
    extraction_failures = 0
    fallback_count = 0
    classifier_calls = 0
    classifier_overrides = 0

    print(f"평가 문장 수: {len(CASES)}\n")

    for index, (case_id, expected, text) in enumerate(CASES, 1):
        outcome = extractor.extract_safe(text)
        expected_value = expected.value
        total_by_type[expected_value] += 1
        total_attempts += outcome.attempt_count
        if outcome.semantic_fallback_applied:
            fallback_count += 1
        classifier_calls += outcome.classification_call_count
        classifier_overrides += int(outcome.classification_override_applied)

        if outcome.result is None:
            predicted = "<EXTRACTION_FAILED>"
            status = "<NO_RESULT>"
            extraction_failures += 1
            detail = outcome.detail or outcome.failure_reason
        else:
            field = outcome.result.technical.issue_type
            predicted = normalized_issue_type(field.value, field.status)
            status = field.status.value
            detail = None

        confusion[expected_value][predicted] += 1
        is_correct = predicted == expected_value
        if is_correct:
            correct_by_type[expected_value] += 1
        else:
            misclassified.append(
                {
                    "case_id": case_id,
                    "expected": expected_value,
                    "predicted": predicted,
                    "status": status,
                    "attempt_count": outcome.attempt_count,
                    "text": text,
                    "detail": detail,
                }
            )

        mark = "PASS" if is_correct else "FAIL"
        print(
            f"[{index:02d}/{len(CASES)}] {mark} {case_id}: "
            f"expected={expected_value}, predicted={predicted}, "
            f"status={status}, attempts={outcome.attempt_count}, "
            f"fallback={outcome.semantic_fallback_applied}, "
            f"classifier_override={outcome.classification_override_applied}"
        )

    total_correct = sum(correct_by_type.values())
    accuracy = total_correct / len(CASES)

    print("\n=== 전체 결과 ===")
    print(f"정답={total_correct}/{len(CASES)}")
    print(f"Accuracy={accuracy:.6f}")
    print(f"추출 실패={extraction_failures}")
    print(f"fallback 적용={fallback_count}")
    print(f"전용 분류 호출={classifier_calls}")
    print(f"전용 분류 override={classifier_overrides}")
    print(f"평균 attempt_count={total_attempts / len(CASES):.6f}")

    print("\n=== 오류유형별 정답률 ===")
    for issue_type in IssueType:
        label = issue_type.value
        correct = correct_by_type[label]
        total = total_by_type[label]
        rate = correct / total if total else 0.0
        print(f"{label}: {correct}/{total} ({rate:.6f})")

    print("\n=== Confusion matrix (정답 -> 예측 분포) ===")
    for issue_type in IssueType:
        expected = issue_type.value
        print(f"{expected} -> {dict(confusion[expected])}")

    print("\n=== 오분류 상세 ===")
    if not misclassified:
        print("없음")
    else:
        for item in misclassified:
            print(
                f"{item['case_id']}: expected={item['expected']}, "
                f"predicted={item['predicted']}, status={item['status']}, "
                f"attempts={item['attempt_count']}"
            )
            print(f"  입력: {item['text']}")
            if item["detail"]:
                print(f"  실패 상세: {item['detail']}")


if __name__ == "__main__":
    main()
