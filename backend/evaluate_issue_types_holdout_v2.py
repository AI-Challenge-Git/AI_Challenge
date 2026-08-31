"""동결된 두 번째 독립 issue_type holdout 평가셋."""

import hashlib
import json
from collections import Counter, defaultdict

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor
from evaluate_issue_types import normalized_issue_type

HOLDOUT_VERSION = "issue-type-holdout-v2-2026-08-25"

# 이 데이터는 실행 결과를 확인한 뒤 문장이나 정답을 수정하지 않는다.
CASES = [
    ("s2_01", IssueType.ORDER_SUBMISSION_FAILURE, "매도 주문을 확정하자 앱 화면이 먹통이 됐어요."),
    (
        "s2_02",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "매수 요청 전송을 누른 뒤 진행 아이콘만 빙글빙글 돕니다.",
    ),
    (
        "s2_03",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "주문 넣으려는데 마지막 확인창에서 더 진행이 안 돼요.",
    ),
    (
        "s2_04",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "데이터 연결은 정상인데 매도 확정 버튼이 아무 반응이 없습니다.",
    ),
    (
        "r2_01",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "매수 주문을 보냈는데 정상적으로 들어갔는지 확인이 안 됩니다.",
    ),
    (
        "r2_02",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "매도한 게 체결된 건지 미체결인지 알 수가 없어요.",
    ),
    (
        "r2_03",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문은 전송했지만 접수 번호가 나타나지 않았습니다.",
    ),
    (
        "r2_04",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "확정을 누른 직후 창이 닫혀 실제 주문이 들어갔는지 모르겠습니다.",
    ),
    (
        "l2_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "공동인증서로 로그인하는 과정에서 인증 오류가 납니다.",
    ),
    ("l2_02", IssueType.LOGIN_ACCESS_FAILURE, "계정 잠금을 해제했는데도 접속이 계속 거절돼요."),
    (
        "l2_03",
        IssueType.LOGIN_ACCESS_FAILURE,
        "로그인 승인 알림이 도착하지 않아 앱에 들어갈 수 없습니다.",
    ),
    (
        "l2_04",
        IssueType.LOGIN_ACCESS_FAILURE,
        "인터넷은 멀쩡한데 아이디로 접속하면 로그인 화면으로 되돌아옵니다.",
    ),
    ("b2_01", IssueType.BALANCE_INQUIRY_ERROR, "계좌의 예수금 금액이 화면에 나타나지 않습니다."),
    (
        "b2_02",
        IssueType.BALANCE_INQUIRY_ERROR,
        "가지고 있던 종목들이 보유 목록에서 전부 사라졌어요.",
    ),
    (
        "b2_03",
        IssueType.BALANCE_INQUIRY_ERROR,
        "오늘 거래된 체결 건을 조회해도 기록을 불러오지 못합니다.",
    ),
    (
        "b2_04",
        IssueType.BALANCE_INQUIRY_ERROR,
        "과거 주문 기록 메뉴를 열면 조회 화면이 먹통입니다.",
    ),
    ("n2_01", IssueType.DEVICE_NETWORK_SUSPECTED, "5G로 전환할 때마다 M-able 연결이 끊어집니다."),
    (
        "n2_02",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "태블릿에서는 되지만 제 스마트폰에서만 앱이 켜지지 않아요.",
    ),
    ("n2_03", IssueType.DEVICE_NETWORK_SUSPECTED, "통신 접속 실패 팝업이 반복해서 표시됩니다."),
    (
        "n2_04",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "와이파이를 사용할 때만 로그인 요청이 시간 초과됩니다.",
    ),
    ("u2_01", IssueType.UNRELATED_OR_AMBIGUOUS, "공동인증서를 갱신하는 절차를 알려주세요."),
    ("u2_02", IssueType.UNRELATED_OR_AMBIGUOUS, "매도 주문을 할 때 거래 비용이 얼마나 드나요?"),
    ("u2_03", IssueType.UNRELATED_OR_AMBIGUOUS, "이번 달 신규 상장 종목 일정을 보고 싶습니다."),
    (
        "u2_04",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "M-able을 새 휴대전화에 설치하는 방법이 궁금합니다.",
    ),
    ("x2_01", IssueType.UNKNOWN, "갑자기 이용이 안 되는데 어느 기능 문제인지는 모르겠습니다."),
    ("x2_02", IssueType.UNKNOWN, "평소와 다르게 화면이 이상하지만 무엇 때문인지는 알 수 없어요."),
    ("x2_03", IssueType.UNKNOWN, "오류 안내가 떴는데 문구와 발생 메뉴는 기억나지 않습니다."),
    ("x2_04", IssueType.UNKNOWN, "처리가 되지 않았지만 어떤 작업 중이었는지는 모르겠습니다."),
]


def dataset_fingerprint():
    payload = [(case_id, expected.value, text) for case_id, expected, text in CASES]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


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

    print(f"holdout version: {HOLDOUT_VERSION}")
    print(f"dataset fingerprint: {dataset_fingerprint()}")
    print(f"평가 문장 수: {len(CASES)}\n")

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
                {
                    "case_id": case_id,
                    "expected": expected_value,
                    "predicted": predicted,
                    "status": status,
                    "attempts": outcome.attempt_count,
                    "fallback": outcome.semantic_fallback_applied,
                    "text": text,
                    "detail": outcome.detail,
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

    total_correct = sum(correct.values())
    accuracy = total_correct / len(CASES)

    print("\n=== 동결 holdout v2 전체 결과 ===")
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
        type_total = totals[label]
        type_correct = correct[label]
        rate = type_correct / type_total if type_total else 0.0
        print(f"{label}: {type_correct}/{type_total} ({rate:.6f})")

    print("\n=== Confusion matrix (정답 -> 예측 분포) ===")
    for issue_type in IssueType:
        label = issue_type.value
        print(f"{label} -> {dict(confusion[label])}")

    print("\n=== 오분류 상세 ===")
    if not misclassified:
        print("없음")
    else:
        for item in misclassified:
            print(
                f"{item['case_id']}: expected={item['expected']}, "
                f"predicted={item['predicted']}, status={item['status']}, "
                f"attempts={item['attempts']}, fallback={item['fallback']}"
            )
            print(f"  입력: {item['text']}")
            if item["detail"]:
                print(f"  실패 상세: {item['detail']}")


if __name__ == "__main__":
    main()
