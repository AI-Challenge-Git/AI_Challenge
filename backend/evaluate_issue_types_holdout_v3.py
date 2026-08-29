"""최종 판정용 동결 issue_type holdout v3."""

import hashlib
import json
from collections import Counter, defaultdict

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor
from evaluate_issue_types import normalized_issue_type

HOLDOUT_VERSION = "issue-type-holdout-v3-2026-08-26"

# 실행 결과를 확인한 뒤 문장, 정답, 합격 기준을 변경하지 않는다.
CASES = [
    (
        "s3_01",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "로그인은 잘 됐는데 최종 주문 확인을 누르자 화면이 고정됐습니다.",
    ),
    (
        "s3_02",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "호가를 입력하고 매수 요청을 보냈는데 전송 단계가 끝나지 않아요.",
    ),
    (
        "s3_03",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "매도 주문 창의 확인 버튼을 터치해도 먹히질 않습니다.",
    ),
    (
        "s3_04",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "인터넷에는 문제가 없지만 주문 승인 화면에서 멈춰 있습니다.",
    ),
    (
        "r3_01",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문 전송은 끝났는데 접수됐다는 표시를 찾을 수 없습니다.",
    ),
    (
        "r3_02",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "매수 요청 이후 주문 번호가 생성됐는지 확인이 안 돼요.",
    ),
    (
        "r3_03",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "매도 신청 결과가 체결인지 취소인지 표시되지 않습니다.",
    ),
    (
        "r3_04",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "주문을 보낸 뒤 성공 여부를 알 수 없어 다시 주문해야 할지 모르겠어요.",
    ),
    (
        "l3_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "간편 비밀번호를 입력하면 다시 로그인 첫 화면으로 돌아옵니다.",
    ),
    ("l3_02", IssueType.LOGIN_ACCESS_FAILURE, "로그인용 일회용 인증 코드가 수신되지 않아요."),
    ("l3_03", IssueType.LOGIN_ACCESS_FAILURE, "지문 인증을 사용한 계정 로그인이 계속 실패합니다."),
    (
        "l3_04",
        IssueType.LOGIN_ACCESS_FAILURE,
        "데이터 통신은 정상인데 계정 인증을 통과하지 못합니다.",
    ),
    (
        "b3_01",
        IssueType.BALANCE_INQUIRY_ERROR,
        "예탁 자산 총액이 어제 금액 그대로라 새로고침되지 않습니다.",
    ),
    (
        "b3_02",
        IssueType.BALANCE_INQUIRY_ERROR,
        "네트워크 오류 안내는 없는데 보유 수량이 0으로 표시됩니다.",
    ),
    (
        "b3_03",
        IssueType.BALANCE_INQUIRY_ERROR,
        "체결조회 메뉴를 열었지만 거래 목록이 하나도 뜨지 않아요.",
    ),
    (
        "b3_04",
        IssueType.BALANCE_INQUIRY_ERROR,
        "지난 주문을 조회하는 화면에서 대기 표시만 반복됩니다.",
    ),
    (
        "n3_01",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "모바일 데이터를 켜면 서버 연결이 자꾸 종료됩니다.",
    ),
    (
        "n3_02",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "회사 무선망에서만 M-able 서버 접속에 실패합니다.",
    ),
    (
        "n3_03",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "동일 계정이 다른 폰에서는 열리는데 현재 단말에서만 실행이 안 됩니다.",
    ),
    (
        "n3_04",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "앱 사용 중 통신 오류 코드와 연결 실패 팝업이 나타납니다.",
    ),
    ("u3_01", IssueType.UNRELATED_OR_AMBIGUOUS, "로그인 OTP를 처음 등록하는 순서가 궁금합니다."),
    (
        "u3_02",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "국내주식을 매도할 때 부과되는 비용을 설명해 주세요.",
    ),
    ("u3_03", IssueType.UNRELATED_OR_AMBIGUOUS, "다음 달 기업공개 예정 종목을 확인하고 싶어요."),
    (
        "u3_04",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "휴대폰을 바꾼 뒤 앱을 이전하는 방법을 알고 싶습니다.",
    ),
    (
        "x3_01",
        IssueType.UNKNOWN,
        "갑자기 뭔가 제대로 처리되지 않는데 어디서 생긴 문제인지 모르겠어요.",
    ),
    (
        "x3_02",
        IssueType.UNKNOWN,
        "서비스 이용 도중 이상 현상이 있었지만 어떤 기능이었는지는 기억나지 않습니다.",
    ),
    (
        "x3_03",
        IssueType.UNKNOWN,
        "화면에 문제가 생겼다가 사라져서 구체적인 상황을 설명하기 어렵습니다.",
    ),
    ("x3_04", IssueType.UNKNOWN, "정상 동작하지 않았다는 것 외에는 확인한 내용이 없습니다."),
]


def dataset_fingerprint():
    payload = [(case_id, expected.value, text) for case_id, expected, text in CASES]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def main():
    extractor = RealDualExtractor()
    confusion = defaultdict(Counter)
    totals = Counter()
    correct = Counter()
    failures = []
    extraction_failures = 0
    fallback_count = 0
    classifier_calls = 0
    classifier_overrides = 0
    total_attempts = 0

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

        passed = predicted == expected_value
        confusion[expected_value][predicted] += 1
        if passed:
            correct[expected_value] += 1
        else:
            failures.append((case_id, expected_value, predicted, status, outcome, text))

        print(
            f"[{index:02d}/{len(CASES)}] {'PASS' if passed else 'FAIL'} "
            f"{case_id}: expected={expected_value}, predicted={predicted}, "
            f"status={status}, attempts={outcome.attempt_count}, "
            f"fallback={outcome.semantic_fallback_applied}, "
            f"classifier_override={outcome.classification_override_applied}"
        )

    total_correct = sum(correct.values())
    accuracy = total_correct / len(CASES)

    print("\n=== 동결 holdout v3 전체 결과 ===")
    print(f"정답={total_correct}/{len(CASES)}")
    print(f"Accuracy={accuracy:.6f}")
    print(f"추출 실패={extraction_failures}")
    print(f"fallback 적용={fallback_count}")
    print(f"전용 분류 호출={classifier_calls}")
    print(f"전용 분류 override={classifier_overrides}")
    print(f"평균 attempt_count={total_attempts / len(CASES):.6f}")

    print("\n=== 오류유형별 정답률 ===")
    per_type_pass = True
    for issue_type in IssueType:
        label = issue_type.value
        type_total = totals[label]
        type_correct = correct[label]
        rate = type_correct / type_total if type_total else 0.0
        per_type_pass &= rate >= 0.75
        print(f"{label}: {type_correct}/{type_total} ({rate:.6f})")

    overall_pass = (
        accuracy >= 0.85
        and extraction_failures == 0
        and per_type_pass
        and total_attempts / len(CASES) <= 1.3
    )
    print("\n=== 최종 합격 판정 ===")
    print("PASS" if overall_pass else "FAIL")

    print("\n=== Confusion matrix (정답 -> 예측 분포) ===")
    for issue_type in IssueType:
        label = issue_type.value
        print(f"{label} -> {dict(confusion[label])}")

    print("\n=== 오분류 상세 ===")
    if not failures:
        print("없음")
    else:
        for case_id, expected, predicted, status, outcome, text in failures:
            print(
                f"{case_id}: expected={expected}, predicted={predicted}, "
                f"status={status}, attempts={outcome.attempt_count}, "
                f"fallback={outcome.semantic_fallback_applied}, "
                f"classifier_override={outcome.classification_override_applied}"
            )
            print(f"  입력: {text}")
            if outcome.detail:
                print(f"  실패 상세: {outcome.detail}")


if __name__ == "__main__":
    main()
