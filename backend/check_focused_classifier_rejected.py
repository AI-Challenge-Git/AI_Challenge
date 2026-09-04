"""
보조 재분류기(_classify_issue_type_focused)의 프롬프트에 "거부"가 명시돼
있지 않은 게 실제로 REJECTED 계열 문장을 잘못 분류하는지 직접 확인한다.

_classify_issue_type_focused()는 메인 추출과 로컬 키워드 후보가 충돌할 때만
호출되는 보조 경로라, 파이프라인 전체를 돌리면 이 경로를 타는지 보장할 수
없다. 그래서 이 함수를 파이프라인과 무관하게 직접 호출해서, 프롬프트
자체가 REJECTED 문장에 대해 ORDER_SUBMISSION_FAILURE를 정확히 내놓는지만
따로 검증한다.

실제 OpenAI API를 호출하므로 비용이 발생한다 (직접 실행해서 확인).
"""

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor

CASES = [
    (
        "잔고 부족 거부",
        "삼성전자 매수 주문을 넣었는데, 매수가능금액이 부족하다며 주문이 거부됐습니다.",
    ),
    (
        "가격 제한 거부",
        "지정가를 상한가보다 높게 넣었더니 가격 범위를 벗어났다며 주문이 거부됐습니다.",
    ),
    (
        "수량 오류 거부",
        "1주 미만으로 매도 주문을 넣었더니 최소 주문 수량 조건에 안 맞는다며 거부됐습니다.",
    ),
    (
        "서버 오류 거부",
        "매수 주문을 넣었는데 서버 통신 오류라는 메시지와 함께 주문이 거부됐습니다.",
    ),
    ("원인 불명 거부", "매도 주문을 넣었는데 이유 설명 없이 그냥 주문이 거부됐다고만 나옵니다."),
    ("앱 강제 종료", "매수 확정 버튼을 누르는 순간 앱이 그대로 꺼져버렸습니다."),
]

extractor = RealDualExtractor()

correct = 0
for label, text in CASES:
    result = extractor._classify_issue_type_focused(text)
    if result is None:
        predicted = None
        evidence = None
    else:
        predicted, evidence = result
    is_correct = predicted is IssueType.ORDER_SUBMISSION_FAILURE
    correct += is_correct
    print(f"--- {label} ---")
    print(f"입력: {text}")
    print(f"보조 분류기 결과: {predicted} (기대: ORDER_SUBMISSION_FAILURE)")
    print(f"evidence_quote: {evidence!r}")
    print(f"정확: {is_correct}")
    print()

print(f"=> {correct}/{len(CASES)} 정확")
