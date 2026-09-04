"""
슬래시 날짜 + 콜론 시각("2026/07/18 23:34") 조합이 실제로 AI 추출에서
CONFIRMED_FROM_TEXT로 확정되는지 확인한다.

실제 OpenAI API를 호출하므로 비용이 발생한다 (직접 실행해서 확인).
"""

from app.real_extractor_v5 import RealDualExtractor

TEXT = "2026/07/18 23:34에 매도 주문을 넣었는데 최소 수량 오류로 거래가 안 됐어요"

extractor = RealDualExtractor()
outcome = extractor.extract_safe(TEXT)

print(f"입력: {TEXT}")
print(f"attempt_count: {outcome.attempt_count}")
print(f"result is None: {outcome.result is None}")
print(f"failure_reason: {outcome.failure_reason}")
print(f"detail (마지막 시도): {(outcome.detail or '')[:500]}")

if outcome.result is not None:
    field = outcome.result.technical.reported_occurred_at
    print(f"\nreported_occurred_at: value={field.value!r} status={field.status} evidence={field.evidence_quote!r}")
