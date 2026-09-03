"""
d160_res_05 하나를 baseline(json_object)/structured(strict schema) 양쪽에서
각각 N회 반복 호출해서 실패율을 직접 측정한다.

dev80 전체 재실행(변수가 많음: 80건 x 여러 issue_type)보다, 문제가 실제로
발생한 이 케이스 하나에 변수를 고정해서 반복하는 쪽이 더 적은 API 비용으로
"structured가 이 케이스를 구조적으로 더 자주 실패시키는가"에 대해 더
명확한 결론을 준다.

REPEAT_COUNT x 2(baseline/structured) API 호출이 발생한다 (직접 실행해서 확인).
"""

import time

from app.real_extractor_v5 import RealDualExtractor

TEXT = "현대차 매도 주문 후 체결 내역에 아무것도 안 보여서 접수 여부를 모르겠어요."
REPEAT_COUNT = 20
CALL_DELAY_SECONDS = 2.0

extractor_baseline = RealDualExtractor(use_structured_output=False)
extractor_structured = RealDualExtractor(use_structured_output=True)

extractors = [
    ("baseline(json_object)", extractor_baseline),
    ("structured(strict schema)", extractor_structured),
]
for label, extractor in extractors:
    print(f"\n=== {label}: {REPEAT_COUNT}회 반복 ===")
    ok_count = 0
    retried_count = 0
    for i in range(REPEAT_COUNT):
        if i > 0:
            time.sleep(CALL_DELAY_SECONDS)
        outcome = extractor.extract_safe(TEXT)
        ok = outcome.result is not None
        ok_count += ok
        if outcome.attempt_count > 1:
            retried_count += 1
        print(
            f"- {i + 1}/{REPEAT_COUNT}: ok={ok} attempt_count={outcome.attempt_count} "
            f"failure_reason={outcome.failure_reason} "
            f"detail={(outcome.detail or '')[:150]}"
        )
    print(f"-> 성공 {ok_count}/{REPEAT_COUNT}, 재시도 발생 {retried_count}/{REPEAT_COUNT}")
