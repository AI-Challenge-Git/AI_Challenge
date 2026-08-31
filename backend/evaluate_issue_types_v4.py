"""
issue_type 분류 v4 평가.

기존 evaluate_issue_types.py / holdout / holdout_v2 / holdout_v3에서 쓴 문장을
전혀 재사용하지 않은 새 문장 28건(오류유형별 4건)으로 구성한다.

기존 holdout들과 달리 정확도뿐 아니라 다음도 함께 측정한다:
- evidence_quote가 masked_text의 정확한 substring인지 (AI-03)
- evidence_quote로 원문 전체를 그대로 쓴 비율 (0%여야 함 — 로컬 규칙이
  issue_type을 직접 확정하던 예전 설계의 회귀 여부를 감시하는 지표)
- 1차 추출만으로 끝났는지, 전용 분류 AI가 개입했는지(classifier_calls),
  개입했다면 실제로 값을 바꿨는지(override) — AI 판단과 안전망 개입을 분리 집계

실행 결과를 본 뒤 문장, 정답, 합격 기준을 변경하지 않는다.
"""

import hashlib
import json
from collections import Counter, defaultdict

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor
from evaluate_issue_types import normalized_issue_type

DATASET_VERSION = "issue-type-v4-2026-08-30-context"

# case_id, expected_issue_type, report_text
#
# 대부분의 문장에 issue_type 판단과 무관한 부가 정보(종목, 수량, 가격, 시각,
# 상황 설명)를 섞어 넣었다. evidence_quote가 문장 전체가 아니라 실제
# 판단 근거가 되는 절만 좁혀서 인용하는지 확인하기 위함이다 (v4 1차 실행에서
# 한 절짜리 단문만 쓴 결과 evidence_quote가 대부분 문장 전체와 같아져,
# 근거를 좁히는 능력을 제대로 측정하지 못했다).
CASES = [
    (
        "sub_v4_01",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "오늘 아침 삼성전자 30주를 지정가로 매도하려고 했는데, "
        "지정가 매도를 확정하려는 순간 앱이 응답하지 않았습니다.",
    ),
    (
        "sub_v4_02",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "카카오 주식 15주를 매수하려던 참이었는데, "
        "체결가를 입력하고 전송을 눌렀더니 다음 화면으로 전혀 넘어가지 않아요.",
    ),
    (
        "sub_v4_03",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "네이버 10주를 시장가로 사려고 했고 휴대폰 통신은 문제없는데, "
        "매수 확정 버튼만 누르면 화면이 멈춰버립니다.",
    ),
    (
        "sub_v4_04",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "SK하이닉스를 팔려고 오후에 접속했는데, "
        "주문서 최종 제출 단계에서 앱이 그대로 굳어버렸어요.",
    ),
    (
        "res_v4_01",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "삼성전자 20주를 7만 원에 매도하려고 아침 9시쯤 시도했는데, "
        "분명히 매도를 눌렀지만 정상적으로 접수됐는지 확인할 방법이 없어요.",
    ),
    (
        "res_v4_02",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "LG화학을 매수한 뒤라서 그런데, 체결 내역에 방금 보낸 주문이 있는지 없는지 안 보입니다.",
    ),
    (
        "res_v4_03",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "오후에 카카오뱅크 5주를 팔려고 했는데, "
        "주문을 넣긴 했지만 성공했다는 알림을 못 받아서 다시 넣어야 할지 모르겠어요.",
    ),
    (
        "res_v4_04",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "현대차 8주를 매수 신청한 후, 처리 상태가 어떻게 됐는지 알 길이 없습니다.",
    ),
    (
        "log_v4_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "매도 주문을 넣으려고 아침에 M-able에 접속했는데, "
        "생체인증을 등록했는데도 로그인할 때마다 실패한다고 나옵니다.",
    ),
    (
        "log_v4_02",
        IssueType.LOGIN_ACCESS_FAILURE,
        "주식을 좀 확인하려고 앱을 켰는데, "
        "간편번호 여섯 자리를 정확히 눌러도 계속 오류라고 표시돼요.",
    ),
    (
        "log_v4_03",
        IssueType.LOGIN_ACCESS_FAILURE,
        "장 시작 전에 미리 들어가려고 했는데, "
        "본인인증 문자가 오지 않아서 앱에 들어갈 수가 없습니다.",
    ),
    (
        "log_v4_04",
        IssueType.LOGIN_ACCESS_FAILURE,
        "잔고를 보려고 공동인증서로 로그인하려는데 인증서 오류만 반복됩니다.",
    ),
    (
        "bal_v4_01",
        IssueType.BALANCE_INQUIRY_ERROR,
        "어제 주식을 판 뒤라서 출금하려고 했는데, 출금 가능 금액이 실제와 다르게 표시됩니다.",
    ),
    (
        "bal_v4_02",
        IssueType.BALANCE_INQUIRY_ERROR,
        "오늘 거래한 내역을 보려고 매매 내역 화면을 열었는데, 로딩만 계속되고 아무것도 안 나와요.",
    ),
    (
        "bal_v4_03",
        IssueType.BALANCE_INQUIRY_ERROR,
        "포트폴리오를 정리하려고 들어갔는데, 보유 종목 목록에 어제 산 주식이 빠져 있습니다.",
    ),
    (
        "bal_v4_04",
        IssueType.BALANCE_INQUIRY_ERROR,
        "추가 매수 전에 여유자금을 확인하려고 예수금 조회를 눌렀는데, "
        "화면이 하얗게 비어서 나옵니다.",
    ),
    (
        "net_v4_01",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "출근길 지하철에서 잔고를 확인하려고 했는데, "
        "데이터 신호가 약해질 때마다 앱 연결이 끊깁니다.",
    ),
    (
        "net_v4_02",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "집에서 태블릿으로는 잘 되는데, 같은 계정으로 이 휴대폰에서만 실행이 안 돼요.",
    ),
    (
        "net_v4_03",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "지난주에 공유기를 새로 바꾼 뒤부터, "
        "다른 앱은 다 되는데 이 앱만 서버 연결에 계속 실패합니다.",
    ),
    (
        "net_v4_04",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "동료 휴대폰에서는 정상적으로 실행되는데, 이 기기에서만 앱이 자꾸 꺼집니다.",
    ),
    (
        "unr_v4_01",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "해외로 송금할 일이 있어서 그런데, 환전 우대율이 몇 퍼센트인지 알고 싶어요.",
    ),
    (
        "unr_v4_02",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "레버리지를 좀 써보려고 하는데, 신용거래 가능 종목을 어디서 볼 수 있는지 궁금합니다.",
    ),
    (
        "unr_v4_03",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "보유 중인 종목이 있어서 그런데, 배당금 지급일이 언제인지 확인하고 싶어요.",
    ),
    (
        "unr_v4_04",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "미국 주식도 사보고 싶은데, 해외주식 계좌를 새로 개설하는 절차를 알려주세요.",
    ),
    (
        "unk_v4_01",
        IssueType.UNKNOWN,
        "어제 앱을 쓰다가 뭔가 평소랑 다르게 이상했는데 정확히 뭐가 문제였는지는 잘 모르겠어요.",
    ),
    (
        "unk_v4_02",
        IssueType.UNKNOWN,
        "장중에 오류가 있었던 것 같은데 다시 보니 괜찮아져서 뭐라고 설명하기 어렵습니다.",
    ),
    (
        "unk_v4_03",
        IssueType.UNKNOWN,
        "오늘 이용 중에 문제가 있었지만 구체적으로 어느 부분인지 기억이 안 납니다.",
    ),
    (
        "unk_v4_04",
        IssueType.UNKNOWN,
        "아까는 잘 안 됐다는 것만 알겠고 나머지는 잘 모르겠습니다.",
    ),
]


def dataset_fingerprint() -> str:
    payload = [(case_id, expected.value, text) for case_id, expected, text in CASES]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def main() -> None:
    extractor = RealDualExtractor()
    confusion: defaultdict[str, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    correct: Counter = Counter()
    failures = []

    extraction_failures = 0
    total_attempts = 0
    classifier_calls_total = 0
    classifier_overrides_total = 0

    evidence_checked = 0
    evidence_substring_ok = 0
    evidence_full_text_count = 0
    full_text_cases: list[str] = []

    print(f"dataset version: {DATASET_VERSION}")
    print(f"dataset fingerprint: {dataset_fingerprint()}")
    print(f"평가 문장 수: {len(CASES)}\n")

    for index, (case_id, expected, text) in enumerate(CASES, 1):
        outcome = extractor.extract_safe(text)
        expected_value = expected.value
        totals[expected_value] += 1
        total_attempts += outcome.attempt_count
        classifier_calls_total += outcome.classification_call_count
        classifier_overrides_total += int(outcome.classification_override_applied)

        if outcome.result is None:
            predicted = "<EXTRACTION_FAILED>"
            status = "<NO_RESULT>"
            extraction_failures += 1
        else:
            field = outcome.result.technical.issue_type
            predicted = normalized_issue_type(field.value, field.status)
            status = field.status.value

            evidence = field.evidence_quote
            if evidence is not None:
                evidence_checked += 1
                if evidence in text:
                    evidence_substring_ok += 1
                if evidence == text:
                    evidence_full_text_count += 1
                    full_text_cases.append(case_id)

        passed = predicted == expected_value
        confusion[expected_value][predicted] += 1
        if passed:
            correct[expected_value] += 1
        else:
            failures.append((case_id, expected_value, predicted, status, text))

        print(
            f"[{index:02d}/{len(CASES)}] {'PASS' if passed else 'FAIL'} "
            f"{case_id}: expected={expected_value}, predicted={predicted}, "
            f"status={status}, attempts={outcome.attempt_count}, "
            f"classifier_calls={outcome.classification_call_count}, "
            f"classifier_override={outcome.classification_override_applied}"
        )

    total_correct = sum(correct.values())
    accuracy = total_correct / len(CASES)
    avg_attempts = total_attempts / len(CASES)
    evidence_substring_rate = evidence_substring_ok / evidence_checked if evidence_checked else 1.0
    full_text_rate = evidence_full_text_count / evidence_checked if evidence_checked else 0.0

    print("\n=== 전체 결과 ===")
    print(f"정답={total_correct}/{len(CASES)}")
    print(f"Accuracy={accuracy:.6f}")
    print(f"추출 실패={extraction_failures}")
    print(f"평균 attempt_count={avg_attempts:.6f}")
    print(f"전용 분류 호출 합계={classifier_calls_total}")
    print(f"전용 분류 override 합계={classifier_overrides_total}")

    print("\n=== evidence_quote 품질 ===")
    print(f"evidence_quote 존재 필드 수={evidence_checked}")
    print(f"substring 검증 통과율={evidence_substring_rate:.6f} (목표: 1.0)")
    print(f"원문 전체를 evidence로 사용한 비율={full_text_rate:.6f} (목표: 0.0)")
    if full_text_cases:
        print(f"원문 전체가 evidence로 쓰인 케이스: {full_text_cases}")

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
        and avg_attempts <= 1.3
        and evidence_substring_rate == 1.0
        and full_text_rate == 0.0
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
        for case_id, expected_value, predicted, status, text in failures:
            print(f"{case_id}: expected={expected_value}, predicted={predicted}, status={status}")
            print(f"  입력: {text}")


if __name__ == "__main__":
    main()
