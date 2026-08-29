from app.real_extractor_v5 import RealDualExtractor

e = RealDualExtractor()

test_cases = [
    "2026년 8월 15일 오전 9시 3분에 삼성전자 매도 주문을 넣었는데 계속 로딩만 됩니다.",
    "26년 8월 15일 오후 8시 25분에 삼성전자 10주를 매도하려고 했는데 무한 로딩만 됨.",
    "오전 9시 3분에 삼성전자 매도 주문을 넣었는데 계속 로딩만 됩니다.",
]

for text in test_cases:
    o = e.extract_safe(text)

    print("입력:", text)
    print("attempt_count:", o.attempt_count)

    if o.result is None:
        print("추출 실패")
        print("failure_reason:", o.failure_reason)
        print("detail:", o.detail)
    else:
        print(
            "reported_occurred_at:",
            o.result.technical.reported_occurred_at,
        )

    print("-" * 120)