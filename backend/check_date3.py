from app.real_extractor_v5 import RealDualExtractor

extractor = RealDualExtractor()

test_cases = [
    "26년 8월 15일 오전 11시에 삼성전자 주식 팔려고 했는데 로딩만 됩니다.",
    "2026년 8월 15일 오전 11시에 삼성전자 주식 팔려고 했는데 로딩만 됩니다.",
]

for text in test_cases:
    print("=" * 80)
    print("입력:", text)
    outcome = extractor.extract_safe(text)
    print("attempt_count:", outcome.attempt_count)
    if outcome.result is None:
        print("추출 실패")
        print("failure_reason:", outcome.failure_reason)
        print("detail:", outcome.detail)
    else:
        field = outcome.result.technical.reported_occurred_at
        print("status:", field.status)
        print("value:", repr(field.value))
        print("evidence_quote:", repr(field.evidence_quote))
