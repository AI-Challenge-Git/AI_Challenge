"""
"26-9-29 8시 23분" 같은 2자리 연도 + 대시 형식 날짜에서 분석이 실패하는지
확인한다. extract_safe()의 1차/2차/3차 시도 raw JSON을 직접 출력해서, AI가
2자리 연도를 4자리로 확장하지 못해 reported_occurred_at 검증에 계속
실패하는지 본다.

실제 OpenAI API를 호출하므로 비용이 발생한다 (직접 실행해서 확인).
"""

from app.real_extractor_v5 import RealDualExtractor

TEXT = "26-9-29 8시 23분 쯤에 매수 주문을 넣었는데 접수됐는지 확인이 안 됩니다."

extractor = RealDualExtractor()
outcome = extractor.extract_safe(TEXT)

print(f"입력: {TEXT}")
print(f"attempt_count: {outcome.attempt_count}")
print(f"result is None: {outcome.result is None}")
print(f"failure_reason: {outcome.failure_reason}")
print(f"first_failure_reason: {outcome.first_failure_reason}")
print(f"first_failure_detail: {(outcome.first_failure_detail or '')[:500]}")
print(f"detail (마지막 시도): {(outcome.detail or '')[:500]}")

if outcome.result is not None:
    field = outcome.result.technical.reported_occurred_at
    print(
        f"\nreported_occurred_at: value={field.value!r} status={field.status} evidence={field.evidence_quote!r}"
    )
