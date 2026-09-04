"""
taxonomy v2에서 새로 추가한 6개 라벨(ORDER_SUBMISSION_FAILURE 하위)이
실제로 의도한 canonical 문구를 뽑아내는지 확인하는 스크립트.

각 라벨을 명확히 겨냥한 문장 1개씩 넣어서, AI가 issue_type=ORDER_SUBMISSION_FAILURE로
분류하고 symptom이 해당 라벨의 canonical 문구와 정확히 일치하는지 본다.

실제 OpenAI API를 호출하므로 비용이 발생한다 (직접 실행해서 확인).
"""

from app.real_extractor_v5 import RealDualExtractor

CASES = [
    (
        "ORDER_REJECTED_INSUFFICIENT_BALANCE",
        "잔고 부족으로 주문이 거부됨",
        "삼성전자 매수 주문을 넣었는데, 매수가능금액이 부족하다며 주문이 거부됐습니다.",
    ),
    (
        "ORDER_REJECTED_PRICE_LIMIT",
        "가격이 제한 범위를 벗어나 주문이 거부됨",
        "지정가를 상한가보다 높게 넣었더니 가격 범위를 벗어났다며 주문이 거부됐습니다.",
    ),
    (
        "ORDER_REJECTED_QUANTITY_INVALID",
        "수량 또는 단위 오류로 주문이 거부됨",
        "1주 미만으로 매도 주문을 넣었더니 최소 주문 수량 조건에 안 맞는다며 거부됐습니다.",
    ),
    (
        "ORDER_REJECTED_SERVER_ERROR",
        "서버·네트워크 오류로 주문이 거부됨",
        "매수 주문을 넣었는데 서버 통신 오류라는 메시지와 함께 주문이 거부됐습니다.",
    ),
    (
        "ORDER_REJECTED_UNKNOWN_REASON",
        "원인을 알 수 없는 오류로 주문이 거부됨",
        "매도 주문을 넣었는데 이유 설명 없이 그냥 주문이 거부됐다고만 나옵니다.",
    ),
    (
        "APP_TERMINATED_DURING_SUBMISSION",
        "주문 중 앱이 강제 종료됨",
        "매수 확정 버튼을 누르는 순간 앱이 그대로 꺼져버렸습니다.",
    ),
]

extractor = RealDualExtractor()

for label, expected_phrase, text in CASES:
    outcome = extractor.extract_safe(text)
    ok = outcome.result is not None
    issue_type = outcome.result.technical.issue_type.value if ok else None
    symptom = outcome.result.technical.symptom.value if ok else None
    matched = symptom == expected_phrase
    print(f"--- {label} ---")
    print(f"입력: {text}")
    print(f"issue_type: {issue_type} (기대: ORDER_SUBMISSION_FAILURE)")
    print(f"symptom: {symptom!r}")
    print(f"기대 문구와 일치: {matched}")
    print()
