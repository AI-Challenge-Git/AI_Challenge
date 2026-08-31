"""튜닝에 사용하지 않은 문장으로 issue_type 일반화 성능을 평가한다."""

from collections import Counter, defaultdict

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor
from evaluate_issue_types import normalized_issue_type

# case_id, expected_issue_type, unseen report_text
CASES = [
    (
        "submission_holdout_01",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "매수 주문 확인을 눌렀지만 화면이 그대로입니다.",
    ),
    (
        "submission_holdout_02",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "주문 전송 화면에서 진행 표시만 계속 돌아갑니다.",
    ),
    (
        "submission_holdout_03",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "와이파이는 정상인데 매도 버튼을 눌러도 반응하지 않습니다.",
    ),
    (
        "result_holdout_01",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "매매 주문을 완료했는데 정상 접수인지 확인되지 않습니다.",
    ),
    (
        "result_holdout_02",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문 후 체결됐는지 알 수 없습니다.",
    ),
    (
        "result_holdout_03",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문 화면이 멈춰 접수 여부를 모르겠습니다.",
    ),
    (
        "login_holdout_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "로그인할 때 자격 증명 오류가 표시됩니다.",
    ),
    (
        "login_holdout_02",
        IssueType.LOGIN_ACCESS_FAILURE,
        "본인인증 문자가 수신되지 않습니다.",
    ),
    (
        "login_holdout_03",
        IssueType.LOGIN_ACCESS_FAILURE,
        "계정 접속이 계속 거부됩니다.",
    ),
    (
        "balance_holdout_01",
        IssueType.BALANCE_INQUIRY_ERROR,
        "계좌 잔액이 이전 값에서 바뀌지 않습니다.",
    ),
    (
        "balance_holdout_02",
        IssueType.BALANCE_INQUIRY_ERROR,
        "매수한 종목이 보유 목록에 나타나지 않습니다.",
    ),
    (
        "balance_holdout_03",
        IssueType.BALANCE_INQUIRY_ERROR,
        "거래 체결 기록을 불러올 수 없습니다.",
    ),
    (
        "network_holdout_01",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "LTE에서만 M-able 연결이 끊어집니다.",
    ),
    (
        "network_holdout_02",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "같은 계정인데 이 휴대폰에서만 앱이 실행되지 않습니다.",
    ),
    (
        "network_holdout_03",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "네트워크 연결 실패 안내가 반복됩니다.",
    ),
    (
        "unrelated_holdout_01",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "비밀번호 변경 방법을 알려주세요.",
    ),
    (
        "unrelated_holdout_02",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "주식 주문 수수료는 얼마인가요?",
    ),
    (
        "unrelated_holdout_03",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "다음 공모주 일정을 확인하고 싶습니다.",
    ),
    (
        "unknown_holdout_01",
        IssueType.UNKNOWN,
        "앱 동작이 평소와 다릅니다.",
    ),
    (
        "unknown_holdout_02",
        IssueType.UNKNOWN,
        "무슨 문제인지 모르겠지만 사용할 수 없습니다.",
    ),
    (
        "unknown_holdout_03",
        IssueType.UNKNOWN,
        "오류가 생겼는데 어느 메뉴인지는 기억나지 않습니다.",
    ),
]


def main():
    extractor = RealDualExtractor()
    confusion = defaultdict(Counter)
    totals = Counter()
    correct = Counter()
    misclassified = []
    extraction_failures = 0
    fallback_count = 0
    total_attempts = 0
    classifier_calls = 0
    classifier_overrides = 0

    print(f"holdout 평가 문장 수: {len(CASES)}\n")

    for index, (case_id, expected, text) in enumerate(CASES, 1):
        outcome = extractor.extract_safe(text)
        expected_value = expected.value
        totals[expected_value] += 1
        total_attempts += outcome.attempt_count
        fallback_count += int(outcome.semantic_fallback_applied)
        classifier_calls += outcome.classification_call_count
        classifier_overrides += int(outcome.classification_override_applied)

        if outcome.result is None:
            predicted = "<EXTRACTION_FAILED>"
            status = "<NO_RESULT>"
            extraction_failures += 1
        else:
            field = outcome.result.technical.issue_type
            predicted = normalized_issue_type(field.value, field.status)
            status = field.status.value

        is_correct = predicted == expected_value
        confusion[expected_value][predicted] += 1
        if is_correct:
            correct[expected_value] += 1
        else:
            misclassified.append(
                (
                    case_id,
                    expected_value,
                    predicted,
                    status,
                    outcome.attempt_count,
                    outcome.semantic_fallback_applied,
                    text,
                    outcome.detail,
                )
            )

        mark = "PASS" if is_correct else "FAIL"
        print(
            f"[{index:02d}/{len(CASES)}] {mark} {case_id}: "
            f"expected={expected_value}, predicted={predicted}, "
            f"status={status}, attempts={outcome.attempt_count}, "
            f"fallback={outcome.semantic_fallback_applied}, "
            f"classifier_override={outcome.classification_override_applied}"
        )

    total_correct = sum(correct.values())
    accuracy = total_correct / len(CASES)

    print("\n=== holdout 전체 결과 ===")
    print(f"정답={total_correct}/{len(CASES)}")
    print(f"Accuracy={accuracy:.6f}")
    print(f"추출 실패={extraction_failures}")
    print(f"fallback 적용={fallback_count}")
    print(f"전용 분류 호출={classifier_calls}")
    print(f"전용 분류 override={classifier_overrides}")
    print(f"평균 attempt_count={total_attempts / len(CASES):.6f}")

    print("\n=== holdout 오류유형별 정답률 ===")
    for issue_type in IssueType:
        label = issue_type.value
        type_total = totals[label]
        type_correct = correct[label]
        rate = type_correct / type_total if type_total else 0.0
        print(f"{label}: {type_correct}/{type_total} ({rate:.6f})")

    print("\n=== holdout Confusion matrix (정답 -> 예측 분포) ===")
    for issue_type in IssueType:
        label = issue_type.value
        print(f"{label} -> {dict(confusion[label])}")

    print("\n=== holdout 오분류 상세 ===")
    if not misclassified:
        print("없음")
    else:
        for (
            case_id,
            expected,
            predicted,
            status,
            attempts,
            fallback,
            text,
            detail,
        ) in misclassified:
            print(
                f"{case_id}: expected={expected}, predicted={predicted}, "
                f"status={status}, attempts={attempts}, fallback={fallback}"
            )
            print(f"  입력: {text}")
            if detail:
                print(f"  실패 상세: {detail}")


if __name__ == "__main__":
    main()
