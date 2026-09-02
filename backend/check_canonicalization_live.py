from app.real_extractor_v5 import RealDualExtractor

extractor = RealDualExtractor()

test_cases = [
    "화면이 계속 안넘어가서 로그인이 안 됩니다.",
    "로딩 표시만 반복되고 계좌화면에 들어갈 수 없어요.",
    "화면이 멈추고 로그인이 되지 않습니다.",
]

results = []
for text in test_cases:
    outcome = extractor.extract_safe(text)
    if outcome.result is None:
        print("입력:", text, "-> 추출 실패:", outcome.failure_reason, outcome.detail)
        continue
    field = outcome.result.technical.symptom
    issue_type_field = outcome.result.technical.issue_type
    print("입력:", text)
    print("  issue_type.value:", repr(issue_type_field.value))
    print("  symptom.value:", repr(field.value))
    print("  status:", field.status)
    results.append(field.value)

print("\n전부 동일한 canonical 문구인가:", len(set(results)) == 1 if results else "N/A")
