"""
동일 입력 반복 실연동 평가 (홍혜원님 요청 D).

같은 문장을 여러 번 넣었을 때:
- INVALID_SCHEMA 등 terminal 실패 비율
- correction retry(attempt_count) 분포
- issue_type/symptom(canonical label) 출력이 매번 같은지(필드별 일관성)
- hard negative(표면 키워드 != 실제 원인) 문장의 정답률
- 평균 latency

전체 160건을 20회씩 돌리면 비용이 크므로, 대표 문장 소수(정상 케이스 + hard
negative 케이스)만 REPEAT_COUNT번 반복한다. 필요하면 CASES 리스트에 문장을
추가해서 범위를 넓힐 수 있다.
"""

import time
from collections import Counter

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor

REPEAT_COUNT = 20
# 호출 사이 딜레이. taxonomy 추가로 프롬프트가 길어져 토큰 사용량이 늘었고,
# 100회를 거의 쉼 없이 쐈더니 rate limit(대부분 PROVIDER_UNAVAILABLE로 뭉뚱그려짐)에
# 걸려 처음 문장 이후 전부 실패했던 회귀를 막기 위한 페이싱이다.
CALL_DELAY_SECONDS = 2.0

# (case_id, expected_issue_type, text, is_hard_negative)
CASES: list[tuple[str, IssueType, str, bool]] = [
    ("clean_login_01", IssueType.LOGIN_ACCESS_FAILURE, "화면이 멈추고 로그인이 되지 않습니다.", False),
    ("clean_order_01", IssueType.ORDER_SUBMISSION_FAILURE, "매수 주문 버튼을 눌렀는데 로딩만 됩니다.", False),
    ("clean_balance_01", IssueType.BALANCE_INQUIRY_ERROR, "잔고 화면이 예전 값 그대로 갱신되지 않습니다.", False),
    (
        "hard_neg_login_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "잔고를 보려고 앱을 켰는데, 간편번호를 눌러도 그 화면 자체를 못 넘어가서 아무것도 확인할 수가 없습니다.",
        True,
    ),
    (
        "hard_neg_network_01",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "매수 주문 버튼 자체는 눌리는데, 지하철 구간에 들어설 때마다 화면이 통째로 멈췄다가 다시 돌아옵니다.",
        True,
    ),
]


def main() -> None:
    extractor = RealDualExtractor()

    print(f"{len(CASES)}개 문장 x {REPEAT_COUNT}회 반복 = 총 {len(CASES) * REPEAT_COUNT}회 호출\n")

    for case_id, expected_issue_type, text, is_hard_negative in CASES:
        issue_types: Counter[str] = Counter()
        symptoms: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        attempt_counts: list[int] = []
        latencies: list[float] = []
        terminal_failures = 0
        failure_reasons: Counter[str] = Counter()
        failure_details: list[str] = []

        for repeat_index in range(REPEAT_COUNT):
            if repeat_index > 0:
                time.sleep(CALL_DELAY_SECONDS)
            started = time.perf_counter()
            outcome = extractor.extract_safe(text)
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            attempt_counts.append(outcome.attempt_count)

            if outcome.result is None:
                terminal_failures += 1
                failure_reasons[str(outcome.failure_reason)] += 1
                if outcome.detail:
                    failure_details.append(outcome.detail)
                continue

            field = outcome.result.technical.issue_type
            issue_types[field.value.value if field.value else "None"] += 1
            symptom_field = outcome.result.technical.symptom
            symptoms[symptom_field.value or "None"] += 1
            statuses[symptom_field.status.value] += 1

        print(f"=== {case_id} ({'hard negative' if is_hard_negative else '일반'}) ===")
        print(f"입력: {text}")
        print(f"기대 issue_type: {expected_issue_type.value}")
        print(f"terminal 실패: {terminal_failures}/{REPEAT_COUNT}")
        if failure_reasons:
            print(f"  실패 사유 분포: {dict(failure_reasons)}")
        if failure_details:
            print(f"  실패 상세(첫 1건): {failure_details[0][:300]}")
        print(f"issue_type 분포: {dict(issue_types)}")
        correct = issue_types.get(expected_issue_type.value, 0)
        print(f"정답률: {correct}/{REPEAT_COUNT} ({correct / REPEAT_COUNT:.1%})")
        print(f"symptom(canonical) 분포: {dict(symptoms)}")
        print(f"symptom status 분포: {dict(statuses)}")
        print(
            f"attempt_count 분포: {dict(Counter(attempt_counts))} "
            f"(평균 {sum(attempt_counts) / len(attempt_counts):.2f})"
        )
        print(
            f"latency: 평균 {sum(latencies) / len(latencies):.2f}s, "
            f"최소 {min(latencies):.2f}s, 최대 {max(latencies):.2f}s"
        )
        print()


if __name__ == "__main__":
    main()
