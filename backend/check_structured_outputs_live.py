"""
real_extractor_v5.RealDualExtractor(use_structured_output=True) 라이브 확인용 스크립트.

이전에 .parse() 방식은 AI-05 위반까지 클라이언트단에서 막아버려서 100% 추출
실패로 회귀했었다. 이번 버전은 .create()를 그대로 쓰고 response_format만
ExtractionResult의 strict JSON schema로 바꾼 것이라, 문법 오류(json.JSONDecodeError)로
인한 실패는 줄어들되 AI-05 같은 비즈니스 규칙 위반은 여전히 기존
correction retry 경로로 처리되어야 한다.

앞선 5문장(쉬운 케이스)은 baseline도 1차에 전부 성공해서 구조화 출력의
효과를 볼 수 없었다. 이번엔 evaluate_issue_types_locked80.py의 hard
negative 14건(issue_type 분류가 실제로 애매해서 correction retry가 걸릴
가능성이 더 높은 케이스)으로 attempt_count/정확도 차이를 본다.

실제 OpenAI API를 호출하므로 비용이 발생한다 (직접 실행해서 확인).
"""

import time

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor
from evaluate_issue_types import normalized_issue_type

# evaluate_issue_types_locked80.py의 l160_hn_* 14건과 동일
HARD_CASES: list[tuple[str, IssueType, str]] = [
    (
        "l160_hn_sub_01",
        "삼성전자 매도 주문을 넣으려고 다시 로그인까지 했는데, 막상 확정 버튼을 누르니 그 화면에서 그대로 멈춰버립니다.",
        IssueType.ORDER_SUBMISSION_FAILURE,
    ),
    (
        "l160_hn_sub_02",
        "장중 내내 시세는 계속 잘 갱신되고 있었는데, 매수 주문 제출 버튼을 누른 순간부터 화면이 굳어서 넘어가지 않습니다.",
        IssueType.ORDER_SUBMISSION_FAILURE,
    ),
    (
        "l160_hn_res_01",
        "삼성전자 매도 확정 버튼을 누르자 화면은 다음 단계로 잘 넘어갔는데, 그 뒤로 접수됐는지 확인할 방법이 없습니다.",
        IssueType.ORDER_RESULT_UNCONFIRMED,
    ),
    (
        "l160_hn_res_02",
        "카카오뱅크 매수를 누르니 앱은 반응해서 로딩 화면까지 지나갔는데, 체결됐는지 미체결인지 그 다음부터 알 수가 없습니다.",
        IssueType.ORDER_RESULT_UNCONFIRMED,
    ),
    (
        "l160_hn_log_01",
        "잔고를 보려고 앱을 켰는데, 간편번호를 눌러도 그 화면 자체를 못 넘어가서 아무것도 확인할 수가 없습니다.",
        IssueType.LOGIN_ACCESS_FAILURE,
    ),
    (
        "l160_hn_log_02",
        "매수 주문을 넣어보려고 접속했는데, 인증서 창에서 오류가 나서 그 다음 화면으로 아예 못 들어갑니다.",
        IssueType.LOGIN_ACCESS_FAILURE,
    ),
    (
        "l160_hn_bal_01",
        "어제 매도 주문이 잘 처리된 걸 알림으로 확인했는데, 오늘 잔고 화면에는 그 내역이 반영되지 않고 예전 숫자 그대로입니다.",
        IssueType.BALANCE_INQUIRY_ERROR,
    ),
    (
        "l160_hn_bal_02",
        "지하철에서도 앱 접속은 끊김 없이 잘 되는데, 보유 종목 목록에 어제 산 주식이 계속 빠져 있습니다.",
        IssueType.BALANCE_INQUIRY_ERROR,
    ),
    (
        "l160_hn_net_01",
        "매수 주문 버튼 자체는 눌리는데, 지하철 구간에 들어설 때마다 화면이 통째로 멈췄다가 다시 돌아옵니다.",
        IssueType.DEVICE_NETWORK_SUSPECTED,
    ),
    (
        "l160_hn_net_02",
        "비밀번호는 정확히 입력하고 있는데, 공유기를 바꾼 그 날부터 이 앱만 접속 자체가 반복해서 끊깁니다.",
        IssueType.DEVICE_NETWORK_SUSPECTED,
    ),
    (
        "l160_hn_unr_01",
        "매수 주문을 넣다가 실수로 잘못 눌렀는데, 이런 경우 취소가 되는 건지 방법을 알고 싶습니다.",
        IssueType.UNRELATED_OR_AMBIGUOUS,
    ),
    (
        "l160_hn_unr_02",
        "로그인할 때 나오는 보안카드 등록이 필수인지 궁금해서 문의드립니다.",
        IssueType.UNRELATED_OR_AMBIGUOUS,
    ),
    (
        "l160_hn_unk_01",
        "아까 매수인가 매도인가 넣으려다가 뭔가 걸렸는데 정확히 어느 단계였는지는 잘 기억이 안 납니다.",
        IssueType.UNKNOWN,
    ),
    (
        "l160_hn_unk_02",
        "로그인하고 나서 뭔가 화면이 이상했던 것 같은데 잔고 쪽이었는지 주문 쪽이었는지 헷갈립니다.",
        IssueType.UNKNOWN,
    ),
]

extractor_baseline = RealDualExtractor(use_structured_output=False)
extractor_structured = RealDualExtractor(use_structured_output=True)

extractors = [
    ("baseline(json_object)", extractor_baseline),
    ("structured(strict schema)", extractor_structured),
]
for label, extractor in extractors:
    print(f"\n=== {label} ===")
    correct = 0
    retried = 0
    failed = 0
    for case_id, text, expected in HARD_CASES:
        outcome = extractor.extract_safe(text)
        ok = outcome.result is not None
        if ok:
            field = outcome.result.technical.issue_type
            predicted = normalized_issue_type(field.value, field.status)
        else:
            predicted = None
        is_correct = ok and predicted == expected.value
        correct += is_correct
        if outcome.attempt_count > 1:
            retried += 1
        if not ok:
            failed += 1
        print(
            f"- {case_id}: correct={is_correct} attempt_count={outcome.attempt_count} "
            f"expected={expected.value} predicted={predicted} "
            f"failure_reason={outcome.failure_reason}"
        )
        time.sleep(1.0)
    print(
        f"-> correct={correct}/{len(HARD_CASES)} retried(attempt_count>1)={retried} failed={failed}"
    )
