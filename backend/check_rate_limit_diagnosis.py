from app.real_extractor_v5 import RealDualExtractor

extractor = RealDualExtractor()
outcome = extractor.extract_safe("화면이 멈추고 로그인이 되지 않습니다.")

print("result is None:", outcome.result is None)
print("failure_reason:", outcome.failure_reason)
print("detail:", outcome.detail)
print("attempt_count:", outcome.attempt_count)
