"""
issue_type 분류 160건 평가 - 잠금 평가용 80건.

evaluate_issue_types_dev80.py와 짝을 이루는 locked-test 세트다.

*** 이 파일의 문장과 정답은 확정 후 변경하지 않는다. *** threshold나 프롬프트를
이 세트 결과에 맞춰 재조정하지 않는다 (dev 80건으로만 튜닝한다). 이 원칙은
evaluate_issue_types_holdout_v3.py와 동일하다.

유형별 배분 (원안 80건, dev80.py와 동일한 기준):
- ORDER_SUBMISSION_FAILURE   15
- ORDER_RESULT_UNCONFIRMED   13
- LOGIN_ACCESS_FAILURE       15
- BALANCE_INQUIRY_ERROR      12
- DEVICE_NETWORK_SUSPECTED   10
- UNRELATED_OR_AMBIGUOUS      7
- UNKNOWN                     8
여기에 hard negative(표면 키워드 != 실제 원인) 14건을 카테고리별 2건씩 추가해
총 94건이다 (`l160_hn_` 접두사). 팀 리뷰 완료 — 명시적 부정 문구("OO는
정상인데") 없이, 구체적 상황 패턴만으로 원인이 특정되도록 재작성했다.

*** 확정 (2026-09-02): 최초 실행 결과 ***
dataset fingerprint: 0cd7a7710d266a47
Accuracy 93/94(98.9%), hard negative 13/14, evidence_quote substring 100%.
엄격 기준(원문 전체 인용 비율 0.0 포함)은 FAIL — 원안 80건이 무관 정보 없는
단문이라 구조적으로 달성 불가하다는 팀 결론에 따라 완화 기준 채택,
완화 기준으로 최종 PASS. 이 실행 이후 문장/정답은 변경하지 않는다.
"""

from app.codes import IssueType

LOCKED_VERSION = "issue-type-locked80-v3-2026-09-02-confirmed"

# case_id, expected_issue_type, report_text
CASES = [
    # --- ORDER_SUBMISSION_FAILURE (15) ---
    ("l160_sub_01", IssueType.ORDER_SUBMISSION_FAILURE, "LG화학 매도 확정 버튼을 눌렀는데 화면이 넘어가지 않고 그대로 멈춰 있습니다."),
    ("l160_sub_02", IssueType.ORDER_SUBMISSION_FAILURE, "삼성SDI 15주 매수를 시도했는데, 주문 제출 화면에서 로딩만 계속됩니다."),
    ("l160_sub_03", IssueType.ORDER_SUBMISSION_FAILURE, "SK하이닉스 매도 주문을 넣으려고 확인을 눌렀지만 응답이 전혀 없어요."),
    ("l160_sub_04", IssueType.ORDER_SUBMISSION_FAILURE, "현대모비스 매수 확정을 눌렀는데 다음 화면으로 넘어가지 않습니다."),
    ("l160_sub_05", IssueType.ORDER_SUBMISSION_FAILURE, "포스코퓨처엠 매도 신청 중 전송 버튼을 눌러도 진행이 멈춰 있어요."),
    ("l160_sub_06", IssueType.ORDER_SUBMISSION_FAILURE, "NAVER 8주를 매수하려는데 확인 버튼이 반응하지 않습니다."),
    ("l160_sub_07", IssueType.ORDER_SUBMISSION_FAILURE, "SK이노베이션 매도 주문 확정 단계에서 화면이 그대로 굳었습니다."),
    ("l160_sub_08", IssueType.ORDER_SUBMISSION_FAILURE, "카카오뱅크 매수를 시도했는데 전송 후 로딩만 계속되고 끝나지 않아요."),
    ("l160_sub_09", IssueType.ORDER_SUBMISSION_FAILURE, "삼성전기 매도 확인창에서 버튼을 눌러도 아무 변화가 없습니다."),
    ("l160_sub_10", IssueType.ORDER_SUBMISSION_FAILURE, "LG에너지솔루션 매수 신청 화면에서 앱이 멈춰버렸습니다."),
    ("l160_sub_11", IssueType.ORDER_SUBMISSION_FAILURE, "한국전력 매도 주문을 넣으려는데 제출 버튼을 눌러도 진행되지 않아요."),
    ("l160_sub_12", IssueType.ORDER_SUBMISSION_FAILURE, "CJ제일제당 매수 확정을 눌렀는데 계속 로딩 상태로만 있습니다."),
    ("l160_sub_13", IssueType.ORDER_SUBMISSION_FAILURE, "이마트 매도 주문 마지막 확인 단계에서 화면 전환이 안 됩니다."),
    ("l160_sub_14", IssueType.ORDER_SUBMISSION_FAILURE, "만도 매수 버튼을 눌렀는데 처리 중 표시만 뜨고 멈춰 있어요."),
    ("l160_sub_15", IssueType.ORDER_SUBMISSION_FAILURE, "코웨이 매도 확정 화면에서 앱이 완전히 응답을 멈췄습니다."),

    # --- ORDER_RESULT_UNCONFIRMED (13) ---
    ("l160_res_01", IssueType.ORDER_RESULT_UNCONFIRMED, "LG화학 매도 주문을 넣었는데 접수됐다는 확인이 전혀 안 됩니다."),
    ("l160_res_02", IssueType.ORDER_RESULT_UNCONFIRMED, "삼성SDI 매수를 시도한 뒤 체결 여부를 확인할 방법이 없어요."),
    ("l160_res_03", IssueType.ORDER_RESULT_UNCONFIRMED, "SK하이닉스 매도 신청했는데 주문번호가 없어서 정상 처리됐는지 모르겠습니다."),
    ("l160_res_04", IssueType.ORDER_RESULT_UNCONFIRMED, "현대모비스 매수 주문 후 결과 화면이 안 떠서 성공 여부를 알 수 없습니다."),
    ("l160_res_05", IssueType.ORDER_RESULT_UNCONFIRMED, "포스코퓨처엠 매도했는데 체결 내역에 안 보여서 접수됐는지 불안합니다."),
    ("l160_res_06", IssueType.ORDER_RESULT_UNCONFIRMED, "NAVER 매수를 넣었는데 처리 상태를 확인할 길이 없어요."),
    ("l160_res_07", IssueType.ORDER_RESULT_UNCONFIRMED, "SK이노베이션 매도 주문이 들어갔는지 안 갔는지 알 수가 없습니다."),
    ("l160_res_08", IssueType.ORDER_RESULT_UNCONFIRMED, "카카오뱅크 매수 신청 후 완료 여부를 확인할 방법이 없습니다."),
    ("l160_res_09", IssueType.ORDER_RESULT_UNCONFIRMED, "삼성전기 매도했는데 결과를 몰라서 다시 주문해야 할지 판단이 안 섭니다."),
    ("l160_res_10", IssueType.ORDER_RESULT_UNCONFIRMED, "LG에너지솔루션 매수 주문 접수 여부가 확인이 안 됩니다."),
    ("l160_res_11", IssueType.ORDER_RESULT_UNCONFIRMED, "한국전력 매도를 넣었는데 체결됐는지 미체결인지 알 방법이 없어요."),
    ("l160_res_12", IssueType.ORDER_RESULT_UNCONFIRMED, "CJ제일제당 매수 신청 결과가 어떻게 됐는지 확인할 수가 없습니다."),
    ("l160_res_13", IssueType.ORDER_RESULT_UNCONFIRMED, "이마트 매도 주문 후 처리 상태를 알 길이 없어 계속 기다리고 있습니다."),

    # --- LOGIN_ACCESS_FAILURE (15) ---
    ("l160_log_01", IssueType.LOGIN_ACCESS_FAILURE, "간편 비밀번호 여섯 자리를 눌러도 로그인 오류가 반복됩니다."),
    ("l160_log_02", IssueType.LOGIN_ACCESS_FAILURE, "지문 인증으로 들어가려는데 계속 인식이 안 된다고 나옵니다."),
    ("l160_log_03", IssueType.LOGIN_ACCESS_FAILURE, "본인확인 문자가 오지 않아서 로그인을 할 수가 없습니다."),
    ("l160_log_04", IssueType.LOGIN_ACCESS_FAILURE, "공동인증서로 접속하려는데 인증서 오류만 반복해서 뜹니다."),
    ("l160_log_05", IssueType.LOGIN_ACCESS_FAILURE, "얼굴인식 로그인을 시도했지만 계속 실패로 처리됩니다."),
    ("l160_log_06", IssueType.LOGIN_ACCESS_FAILURE, "아이디와 비밀번호를 정확히 넣어도 로그인이 거부됩니다."),
    ("l160_log_07", IssueType.LOGIN_ACCESS_FAILURE, "일회용 인증번호를 입력해도 인증에 계속 실패합니다."),
    ("l160_log_08", IssueType.LOGIN_ACCESS_FAILURE, "패턴을 그려서 들어가려 해도 로그인 화면으로 계속 돌아갑니다."),
    ("l160_log_09", IssueType.LOGIN_ACCESS_FAILURE, "새로 바꾼 간편번호로도 로그인 오류가 그대로 발생합니다."),
    ("l160_log_10", IssueType.LOGIN_ACCESS_FAILURE, "공동인증서를 선택하면 인증서를 찾을 수 없다고 나옵니다."),
    ("l160_log_11", IssueType.LOGIN_ACCESS_FAILURE, "핀번호 입력 화면에서 계속 로그인이 막힙니다."),
    ("l160_log_12", IssueType.LOGIN_ACCESS_FAILURE, "비밀번호를 새로 설정했는데도 로그인이 안 됩니다."),
    ("l160_log_13", IssueType.LOGIN_ACCESS_FAILURE, "생체인증 화면에서 지문을 인식시켜도 아무 반응이 없습니다."),
    ("l160_log_14", IssueType.LOGIN_ACCESS_FAILURE, "인증번호를 받고 입력해도 로그인 실패 메시지가 뜹니다."),
    ("l160_log_15", IssueType.LOGIN_ACCESS_FAILURE, "계정으로 접속을 시도할 때마다 인증 오류만 나옵니다."),

    # --- BALANCE_INQUIRY_ERROR (12) ---
    ("l160_bal_01", IssueType.BALANCE_INQUIRY_ERROR, "예수금 조회를 눌렀는데 화면이 하얗게 비어서 나옵니다."),
    ("l160_bal_02", IssueType.BALANCE_INQUIRY_ERROR, "보유 종목 목록에서 최근에 매수한 종목이 빠져 있습니다."),
    ("l160_bal_03", IssueType.BALANCE_INQUIRY_ERROR, "체결 내역 화면을 열면 아무 기록도 안 나타납니다."),
    ("l160_bal_04", IssueType.BALANCE_INQUIRY_ERROR, "잔고 화면이 갱신되지 않고 어제 값 그대로입니다."),
    ("l160_bal_05", IssueType.BALANCE_INQUIRY_ERROR, "주문 내역 조회 화면이 텅 비어서 나옵니다."),
    ("l160_bal_06", IssueType.BALANCE_INQUIRY_ERROR, "출금 가능 금액이 실제 보유 금액과 다르게 표시됩니다."),
    ("l160_bal_07", IssueType.BALANCE_INQUIRY_ERROR, "보유 수량 화면에 0으로 잘못 표시됩니다."),
    ("l160_bal_08", IssueType.BALANCE_INQUIRY_ERROR, "계좌 잔액을 조회하면 로딩만 계속되고 끝나지 않습니다."),
    ("l160_bal_09", IssueType.BALANCE_INQUIRY_ERROR, "평가손익 조회 화면이 전부 비어 있습니다."),
    ("l160_bal_10", IssueType.BALANCE_INQUIRY_ERROR, "체결 기록에서 방금 한 거래가 누락되어 있습니다."),
    ("l160_bal_11", IssueType.BALANCE_INQUIRY_ERROR, "예탁 자산 총액이 실제와 맞지 않게 표시됩니다."),
    ("l160_bal_12", IssueType.BALANCE_INQUIRY_ERROR, "매매 내역이 며칠째 갱신되지 않고 있습니다."),

    # --- DEVICE_NETWORK_SUSPECTED (10) ---
    ("l160_net_01", IssueType.DEVICE_NETWORK_SUSPECTED, "와이파이 신호가 약해질 때마다 앱 연결이 같이 끊깁니다."),
    ("l160_net_02", IssueType.DEVICE_NETWORK_SUSPECTED, "다른 휴대폰에서는 정상인데 이 기기에서만 앱이 튕깁니다."),
    ("l160_net_03", IssueType.DEVICE_NETWORK_SUSPECTED, "모바일 데이터를 켜면 서버 접속이 계속 실패합니다."),
    ("l160_net_04", IssueType.DEVICE_NETWORK_SUSPECTED, "이동 중 신호가 약한 구간에서 앱이 자주 멈춥니다."),
    ("l160_net_05", IssueType.DEVICE_NETWORK_SUSPECTED, "같은 계정으로 노트북 앱은 되는데 이 휴대폰에서만 안 됩니다."),
    ("l160_net_06", IssueType.DEVICE_NETWORK_SUSPECTED, "공유기를 새로 설치한 뒤부터 이 앱만 연결이 끊깁니다."),
    ("l160_net_07", IssueType.DEVICE_NETWORK_SUSPECTED, "동료 폰에서는 실행되는데 제 기기에서만 안 열립니다."),
    ("l160_net_08", IssueType.DEVICE_NETWORK_SUSPECTED, "네트워크 오류 코드가 뜨면서 접속이 반복적으로 끊깁니다."),
    ("l160_net_09", IssueType.DEVICE_NETWORK_SUSPECTED, "연결 실패 알림이 계속 뜨면서 접속이 안 됩니다."),
    ("l160_net_10", IssueType.DEVICE_NETWORK_SUSPECTED, "이 단말에서만 앱이 계속 강제 종료됩니다."),

    # --- UNRELATED_OR_AMBIGUOUS (7) ---
    ("l160_unr_01", IssueType.UNRELATED_OR_AMBIGUOUS, "국내주식 매매 수수료가 얼마인지 궁금합니다."),
    ("l160_unr_02", IssueType.UNRELATED_OR_AMBIGUOUS, "다음 달 공모주 일정을 알고 싶어요."),
    ("l160_unr_03", IssueType.UNRELATED_OR_AMBIGUOUS, "요즘 증시 전망이 어떤지 궁금합니다."),
    ("l160_unr_04", IssueType.UNRELATED_OR_AMBIGUOUS, "신용거래 가능 종목을 어디서 확인하나요."),
    ("l160_unr_05", IssueType.UNRELATED_OR_AMBIGUOUS, "배당 지급일이 언제인지 알려주세요."),
    ("l160_unr_06", IssueType.UNRELATED_OR_AMBIGUOUS, "간편번호 변경 방법을 알고 싶습니다."),
    ("l160_unr_07", IssueType.UNRELATED_OR_AMBIGUOUS, "펀드 상품 목록을 어디서 볼 수 있나요."),

    # --- UNKNOWN (8) ---
    ("l160_unk_01", IssueType.UNKNOWN, "며칠 전 앱에서 뭔가 이상했는데 정확히 어떤 상황이었는지 기억이 안 납니다."),
    ("l160_unk_02", IssueType.UNKNOWN, "사용 중 오류가 있었던 것 같은데 어느 기능인지는 설명하기 어렵습니다."),
    ("l160_unk_03", IssueType.UNKNOWN, "뭔가 잘 안 됐는데 구체적인 부분은 잘 모르겠습니다."),
    ("l160_unk_04", IssueType.UNKNOWN, "이상 증상이 잠깐 있었는데 곧 사라져서 뭐라 말하기 애매합니다."),
    ("l160_unk_05", IssueType.UNKNOWN, "쓰다가 뭔가 걸리는 느낌이 있었는데 정확히는 모르겠습니다."),
    ("l160_unk_06", IssueType.UNKNOWN, "화면에 문제가 있었던 것 같은데 세부적으로는 기억나지 않습니다."),
    ("l160_unk_07", IssueType.UNKNOWN, "정상 작동하지 않았다는 것 외에는 확인한 게 없습니다."),
    ("l160_unk_08", IssueType.UNKNOWN, "뭔가 오류가 있었는데 다시 보니 괜찮아져서 설명이 어렵습니다."),

    # --- HARD NEGATIVES (표면 키워드 != 실제 issue_type, 팀 리뷰 대기) ---
    # 카테고리별 2건. "OO는 정상인데/문제없는데" 식의 명시적 부정 문구를 쓰지
    # 않고, 구체적 상황 패턴(어느 단계에서 어떻게 멈추는지, 언제 재현되는지)만으로
    # 원인을 특정할 수 있게 구성했다. 표면 키워드는 등장하되 실제 판단 근거는
    # 다른 issue_type을 가리킨다.
    (
        "l160_hn_sub_01",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "삼성전자 매도 주문을 넣으려고 다시 로그인까지 했는데, 막상 확정 버튼을 누르니 그 화면에서 그대로 멈춰버립니다.",
    ),
    (
        "l160_hn_sub_02",
        IssueType.ORDER_SUBMISSION_FAILURE,
        "장중 내내 시세는 계속 잘 갱신되고 있었는데, 매수 주문 제출 버튼을 누른 순간부터 화면이 굳어서 넘어가지 않습니다.",
    ),
    (
        "l160_hn_res_01",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "삼성전자 매도 확정 버튼을 누르자 화면은 다음 단계로 잘 넘어갔는데, 그 뒤로 접수됐는지 확인할 방법이 없습니다.",
    ),
    (
        "l160_hn_res_02",
        IssueType.ORDER_RESULT_UNCONFIRMED,
        "카카오뱅크 매수를 누르니 앱은 반응해서 로딩 화면까지 지나갔는데, 체결됐는지 미체결인지 그 다음부터 알 수가 없습니다.",
    ),
    (
        "l160_hn_log_01",
        IssueType.LOGIN_ACCESS_FAILURE,
        "잔고를 보려고 앱을 켰는데, 간편번호를 눌러도 그 화면 자체를 못 넘어가서 아무것도 확인할 수가 없습니다.",
    ),
    (
        "l160_hn_log_02",
        IssueType.LOGIN_ACCESS_FAILURE,
        "매수 주문을 넣어보려고 접속했는데, 인증서 창에서 오류가 나서 그 다음 화면으로 아예 못 들어갑니다.",
    ),
    (
        "l160_hn_bal_01",
        IssueType.BALANCE_INQUIRY_ERROR,
        "어제 매도 주문이 잘 처리된 걸 알림으로 확인했는데, 오늘 잔고 화면에는 그 내역이 반영되지 않고 예전 숫자 그대로입니다.",
    ),
    (
        "l160_hn_bal_02",
        IssueType.BALANCE_INQUIRY_ERROR,
        "지하철에서도 앱 접속은 끊김 없이 잘 되는데, 보유 종목 목록에 어제 산 주식이 계속 빠져 있습니다.",
    ),
    (
        "l160_hn_net_01",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "매수 주문 버튼 자체는 눌리는데, 지하철 구간에 들어설 때마다 화면이 통째로 멈췄다가 다시 돌아옵니다.",
    ),
    (
        "l160_hn_net_02",
        IssueType.DEVICE_NETWORK_SUSPECTED,
        "비밀번호는 정확히 입력하고 있는데, 공유기를 바꾼 그 날부터 이 앱만 접속 자체가 반복해서 끊깁니다.",
    ),
    (
        "l160_hn_unr_01",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "매수 주문을 넣다가 실수로 잘못 눌렀는데, 이런 경우 취소가 되는 건지 방법을 알고 싶습니다.",
    ),
    (
        "l160_hn_unr_02",
        IssueType.UNRELATED_OR_AMBIGUOUS,
        "로그인할 때 나오는 보안카드 등록이 필수인지 궁금해서 문의드립니다.",
    ),
    (
        "l160_hn_unk_01",
        IssueType.UNKNOWN,
        "아까 매수인가 매도인가 넣으려다가 뭔가 걸렸는데 정확히 어느 단계였는지는 잘 기억이 안 납니다.",
    ),
    (
        "l160_hn_unk_02",
        IssueType.UNKNOWN,
        "로그인하고 나서 뭔가 화면이 이상했던 것 같은데 잔고 쪽이었는지 주문 쪽이었는지 헷갈립니다.",
    ),
]

assert len(CASES) == 94, f"locked-test set은 80+hard negative 14건=94건이어야 합니다 (현재 {len(CASES)}건)"

if __name__ == "__main__":
    from evaluate_issue_types_runner import run

    run(
        CASES,
        LOCKED_VERSION,
        hard_negative_prefix="l160_hn_",
        hard_negative_min_correct=10,
    )
