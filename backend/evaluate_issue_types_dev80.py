"""
issue_type 분류 160건 평가 - 개발용 80건.

3차 회의 문서 17.1절 원안(160건, 6종 분류)을 현재 코드의 7종 IssueType
taxonomy에 맞게 재배분했다. 원안의 "무관·모호·중복·공격 입력" 30건은
UNRELATED_OR_AMBIGUOUS/UNKNOWN 두 상태로 분리했다.

유형별 배분 (dev 80 / locked-test 80, 총 160):
- ORDER_SUBMISSION_FAILURE   30 (dev 15 / test 15)
- ORDER_RESULT_UNCONFIRMED   25 (dev 12 / test 13)
- LOGIN_ACCESS_FAILURE       30 (dev 15 / test 15)
- BALANCE_INQUIRY_ERROR      25 (dev 13 / test 12)
- DEVICE_NETWORK_SUSPECTED   20 (dev 10 / test 10)
- UNRELATED_OR_AMBIGUOUS     15 (dev 8 / test 7)
- UNKNOWN                    15 (dev 7 / test 8)

이 파일(dev 80건)은 자유롭게 반복 실행하며 프롬프트/파라미터를 튜닝하는
용도다. locked-test 80건(evaluate_issue_types_locked80.py)은 별도 파일이며
한 번 확정하면 재조정하지 않는다.

확정 (2026-09-02): 기존 evaluate_issue_types.py / holdout / holdout_v2 /
holdout_v3 / v4 / test_*.py의 문장과 겹치지 않음(자동 중복 검사 통과), 무관
정보 보강(BALANCE/NETWORK/UNRELATED 카테고리) 완료. 최종 실행 결과:
Accuracy 80/80(100%), evidence_quote substring 100%, 원문 전체 인용 비율
0.0(목표 달성), 오류유형별 정답률 전 카테고리 100% — 최종 PASS.
hard negative는 이 dev80이 아니라 locked80에 카테고리별 2건씩 별도 추가함
(evaluate_issue_types_locked80.py 참고).
"""

from app.codes import IssueType

DATASET_VERSION = "issue-type-dev80-v1-2026-08-30-draft"

# case_id, expected_issue_type, report_text
CASES = [
    # --- ORDER_SUBMISSION_FAILURE (15) ---
    ("d160_sub_01", IssueType.ORDER_SUBMISSION_FAILURE, "삼성전자 매수 주문을 넣으려고 확인 버튼을 눌렀는데 화면이 그대로 멈춰 있습니다."),
    ("d160_sub_02", IssueType.ORDER_SUBMISSION_FAILURE, "SK텔레콤 20주를 지정가로 매도하려 했는데, 주문 전송 버튼을 눌러도 아무 반응이 없어요."),
    ("d160_sub_03", IssueType.ORDER_SUBMISSION_FAILURE, "포스코 매수 확정을 누르자 로딩 아이콘만 계속 돌고 다음 단계로 안 넘어갑니다."),
    ("d160_sub_04", IssueType.ORDER_SUBMISSION_FAILURE, "네이버 10주 매도를 시도했는데, 최종 확인 화면에서 앱이 완전히 멈췄습니다."),
    ("d160_sub_05", IssueType.ORDER_SUBMISSION_FAILURE, "카카오 주식을 매수하려고 했고 신호도 괜찮은데, 주문 제출 창이 응답하지 않습니다."),
    ("d160_sub_06", IssueType.ORDER_SUBMISSION_FAILURE, "현대차 매도 주문을 넣는 중에 확정 버튼을 눌러도 화면 전환이 안 됩니다."),
    ("d160_sub_07", IssueType.ORDER_SUBMISSION_FAILURE, "LG전자 5주를 시장가로 사려고 했는데, 매수 버튼을 터치해도 진행이 안 돼요."),
    ("d160_sub_08", IssueType.ORDER_SUBMISSION_FAILURE, "기아 매도 확인 단계에서 화면이 멈춰서 몇 분째 그대로입니다."),
    ("d160_sub_09", IssueType.ORDER_SUBMISSION_FAILURE, "삼성바이오로직스 주문을 넣으려는데 전송 버튼이 눌리지 않는 것처럼 반응이 없습니다."),
    ("d160_sub_10", IssueType.ORDER_SUBMISSION_FAILURE, "셀트리온 매수 주문 확정을 눌렀지만 다음 화면으로 진행되지 않고 그대로 멈춰 있어요."),
    ("d160_sub_11", IssueType.ORDER_SUBMISSION_FAILURE, "한화솔루션 매도 신청 화면에서 확인 버튼을 눌렀는데 계속 로딩 표시만 나옵니다."),
    ("d160_sub_12", IssueType.ORDER_SUBMISSION_FAILURE, "두산에너빌리티를 매수하려고 확정을 눌렀는데 앱이 먹통이 됐습니다."),
    ("d160_sub_13", IssueType.ORDER_SUBMISSION_FAILURE, "롯데케미칼 매도 주문을 제출했는데 진행 상태 표시만 반복되고 끝나지 않습니다."),
    ("d160_sub_14", IssueType.ORDER_SUBMISSION_FAILURE, "아모레퍼시픽 매수 확인창에서 버튼을 눌러도 화면이 그대로예요."),
    ("d160_sub_15", IssueType.ORDER_SUBMISSION_FAILURE, "KB금융 매도 주문 마지막 단계에서 앱이 완전히 굳어버렸습니다."),

    # --- ORDER_RESULT_UNCONFIRMED (12) ---
    ("d160_res_01", IssueType.ORDER_RESULT_UNCONFIRMED, "아침에 급하게 삼성전자 매도 주문을 넣었는데, 접수가 됐는지 확인할 방법이 없습니다."),
    ("d160_res_02", IssueType.ORDER_RESULT_UNCONFIRMED, "카카오 10주 매수를 신청했는데 체결됐는지 미체결인지 알 수가 없어요."),
    ("d160_res_03", IssueType.ORDER_RESULT_UNCONFIRMED, "SK텔레콤을 매도했는데 주문 목록에 안 떠서 처리가 됐는지 모르겠습니다."),
    ("d160_res_04", IssueType.ORDER_RESULT_UNCONFIRMED, "네이버 매수를 시도한 뒤 결과 화면이 안 나와서 성공했는지 확인이 안 됩니다."),
    ("d160_res_05", IssueType.ORDER_RESULT_UNCONFIRMED, "현대차 매도 주문 후 체결 내역에 아무것도 안 보여서 접수 여부를 모르겠어요."),
    ("d160_res_06", IssueType.ORDER_RESULT_UNCONFIRMED, "포스코 매수를 넣었는데 주문번호를 못 봐서 정상 처리됐는지 불안합니다."),
    ("d160_res_07", IssueType.ORDER_RESULT_UNCONFIRMED, "LG전자 매도 신청했는데 완료됐다는 표시가 안 나와서 다시 넣어야 할지 모르겠습니다."),
    ("d160_res_08", IssueType.ORDER_RESULT_UNCONFIRMED, "장중에 기아 매수 주문을 넣은 뒤, 진행 상태를 확인할 방법이 없어서 답답합니다."),
    ("d160_res_09", IssueType.ORDER_RESULT_UNCONFIRMED, "삼성바이오로직스 매도했는데 접수 확인이 안 돼서 재주문해야 할지 고민입니다."),
    ("d160_res_10", IssueType.ORDER_RESULT_UNCONFIRMED, "셀트리온 매수를 넣었는데 결과를 알 수 없어 계속 기다리고 있습니다."),
    ("d160_res_11", IssueType.ORDER_RESULT_UNCONFIRMED, "한화솔루션 매도 주문이 실제로 들어갔는지 확인할 길이 없습니다."),
    ("d160_res_12", IssueType.ORDER_RESULT_UNCONFIRMED, "두산에너빌리티 매수 신청 후 처리 여부를 알 수가 없어요."),

    # --- LOGIN_ACCESS_FAILURE (15) ---
    ("d160_log_01", IssueType.LOGIN_ACCESS_FAILURE, "간편 비밀번호를 입력해도 계속 로그인 실패라고 나옵니다."),
    ("d160_log_02", IssueType.LOGIN_ACCESS_FAILURE, "지문 인증을 등록했는데 로그인할 때마다 인식이 안 된다고 나와요."),
    ("d160_log_03", IssueType.LOGIN_ACCESS_FAILURE, "본인인증 문자가 도착하지 않아서 로그인을 진행할 수가 없습니다."),
    ("d160_log_04", IssueType.LOGIN_ACCESS_FAILURE, "공동인증서 비밀번호를 정확히 입력해도 인증 오류가 뜹니다."),
    ("d160_log_05", IssueType.LOGIN_ACCESS_FAILURE, "얼굴 인식 로그인을 시도했는데 계속 인식 실패로 접속이 안 됩니다."),
    ("d160_log_06", IssueType.LOGIN_ACCESS_FAILURE, "계정 아이디로 로그인하려는데 비밀번호가 틀렸다는 메시지만 반복됩니다."),
    ("d160_log_07", IssueType.LOGIN_ACCESS_FAILURE, "OTP 번호를 입력해도 인증에 계속 실패한다고 나옵니다."),
    ("d160_log_08", IssueType.LOGIN_ACCESS_FAILURE, "패턴 잠금을 풀고 들어가려는데 로그인 화면에서 계속 튕깁니다."),
    ("d160_log_09", IssueType.LOGIN_ACCESS_FAILURE, "새로 등록한 간편번호로 로그인해도 오류가 반복됩니다."),
    ("d160_log_10", IssueType.LOGIN_ACCESS_FAILURE, "인증서 로그인 시도마다 인증서를 찾을 수 없다는 오류가 뜹니다."),
    ("d160_log_11", IssueType.LOGIN_ACCESS_FAILURE, "핀번호를 눌러도 로그인이 계속 거부됩니다."),
    ("d160_log_12", IssueType.LOGIN_ACCESS_FAILURE, "비밀번호 재설정 후에도 여전히 로그인이 안 됩니다."),
    ("d160_log_13", IssueType.LOGIN_ACCESS_FAILURE, "생체인증 로그인 화면에서 지문을 대도 반응이 없습니다."),
    ("d160_log_14", IssueType.LOGIN_ACCESS_FAILURE, "인증번호를 받아 입력했는데도 로그인 오류가 계속 뜹니다."),
    ("d160_log_15", IssueType.LOGIN_ACCESS_FAILURE, "계정 접속을 시도할 때마다 인증 실패 메시지만 나옵니다."),

    # --- BALANCE_INQUIRY_ERROR (13) ---
    ("d160_bal_01", IssueType.BALANCE_INQUIRY_ERROR, "추가 매수를 고민하다가 예수금 조회 화면을 열었는데, 금액이 표시되지 않습니다."),
    ("d160_bal_02", IssueType.BALANCE_INQUIRY_ERROR, "포트폴리오를 정리하려고 들어갔는데, 보유 종목 목록에 어제 산 주식이 안 보입니다."),
    ("d160_bal_03", IssueType.BALANCE_INQUIRY_ERROR, "오늘 거래한 게 맞는지 확인하려고 체결 내역 조회를 눌렀는데, 빈 화면만 나옵니다."),
    ("d160_bal_04", IssueType.BALANCE_INQUIRY_ERROR, "잔고 화면이 갱신되지 않고 예전 정보만 계속 뜹니다."),
    ("d160_bal_05", IssueType.BALANCE_INQUIRY_ERROR, "장 마감 후에 주문 내역을 확인하려는데, 목록이 하나도 안 뜹니다."),
    ("d160_bal_06", IssueType.BALANCE_INQUIRY_ERROR, "주식을 판 뒤 출금하려고 봤더니, 출금 가능 금액이 실제와 다르게 표시됩니다."),
    ("d160_bal_07", IssueType.BALANCE_INQUIRY_ERROR, "얼마나 갖고 있는지 보려고 들어갔는데, 보유 수량이 화면에서 0으로 나옵니다."),
    ("d160_bal_08", IssueType.BALANCE_INQUIRY_ERROR, "이체하기 전에 확인차 들어갔는데, 계좌 잔액 조회가 계속 로딩만 되고 안 끝납니다."),
    ("d160_bal_09", IssueType.BALANCE_INQUIRY_ERROR, "오늘 수익이 궁금해서 평가손익 화면을 열면, 숫자가 전부 비어 있습니다."),
    ("d160_bal_10", IssueType.BALANCE_INQUIRY_ERROR, "세금 신고 때문에 체결 기록을 조회했는데, 최근 거래가 빠져 있습니다."),
    ("d160_bal_11", IssueType.BALANCE_INQUIRY_ERROR, "예탁 자산 총액이 실제 잔고와 다르게 나옵니다."),
    ("d160_bal_12", IssueType.BALANCE_INQUIRY_ERROR, "거래 확인차 들어갔는데, 매매 내역 화면이 며칠째 갱신되지 않습니다."),
    ("d160_bal_13", IssueType.BALANCE_INQUIRY_ERROR, "장 시작 전에 확인하려고 들어갔는데, 보유 종목 조회 화면에서 계속 오류 메시지가 뜹니다."),

    # --- DEVICE_NETWORK_SUSPECTED (10) ---
    ("d160_net_01", IssueType.DEVICE_NETWORK_SUSPECTED, "집에서 사용하다 보면, 와이파이가 끊길 때마다 앱 연결도 같이 끊어집니다."),
    ("d160_net_02", IssueType.DEVICE_NETWORK_SUSPECTED, "이 휴대폰에서만 앱이 자꾸 튕기고 다른 기기에서는 잘 됩니다."),
    ("d160_net_03", IssueType.DEVICE_NETWORK_SUSPECTED, "와이파이가 안 잡히는 곳에서 모바일 데이터로 전환하면, 서버 연결에 계속 실패합니다."),
    ("d160_net_04", IssueType.DEVICE_NETWORK_SUSPECTED, "출근길에 확인하려고 하면, 지하철에서 신호가 약해질 때마다 앱이 멈춥니다."),
    ("d160_net_05", IssueType.DEVICE_NETWORK_SUSPECTED, "여러 기기로 확인해봤는데, 같은 계정을 태블릿에서 쓰면 정상인데 이 폰에서만 안 됩니다."),
    ("d160_net_06", IssueType.DEVICE_NETWORK_SUSPECTED, "다른 앱들은 문제없이 잘 되는데, 공유기를 바꾼 뒤로 이 앱만 연결이 계속 끊깁니다."),
    ("d160_net_07", IssueType.DEVICE_NETWORK_SUSPECTED, "다른 사람 휴대폰에서는 되는데 제 기기에서만 실행이 안 됩니다."),
    ("d160_net_08", IssueType.DEVICE_NETWORK_SUSPECTED, "장중에 시세를 보다가, 통신 오류 메시지가 뜨면서 앱 접속이 자꾸 끊깁니다."),
    ("d160_net_09", IssueType.DEVICE_NETWORK_SUSPECTED, "주문을 넣으려던 참인데, 네트워크 연결 실패 팝업이 계속 나타납니다."),
    ("d160_net_10", IssueType.DEVICE_NETWORK_SUSPECTED, "다른 사람 휴대폰에서는 괜찮다고 하는데, 이 단말기에서만 앱이 반복적으로 강제 종료됩니다."),

    # --- UNRELATED_OR_AMBIGUOUS (8) ---
    ("d160_unr_01", IssueType.UNRELATED_OR_AMBIGUOUS, "해외주식 거래 수수료가 어느 정도인지 알고 싶습니다."),
    ("d160_unr_02", IssueType.UNRELATED_OR_AMBIGUOUS, "새로 상장하는 종목이 있다고 들었는데, 공모주 청약 일정이 언제인지 확인하고 싶어요."),
    ("d160_unr_03", IssueType.UNRELATED_OR_AMBIGUOUS, "오늘 코스피 전망이 어떤지 궁금합니다."),
    ("d160_unr_04", IssueType.UNRELATED_OR_AMBIGUOUS, "신용거래 신청 방법을 알려주세요."),
    ("d160_unr_05", IssueType.UNRELATED_OR_AMBIGUOUS, "보유 중인 종목이 있어서 그런데, 배당금이 언제 지급되는지 궁금합니다."),
    ("d160_unr_06", IssueType.UNRELATED_OR_AMBIGUOUS, "계좌 비밀번호 변경하는 방법을 알고 싶어요."),
    ("d160_unr_07", IssueType.UNRELATED_OR_AMBIGUOUS, "미국 주식도 사보고 싶은데, 해외주식 계좌 개설 절차가 궁금합니다."),
    ("d160_unr_08", IssueType.UNRELATED_OR_AMBIGUOUS, "매달 조금씩 투자해보고 싶은데, 적립식 투자 상품을 어디서 볼 수 있는지 알려주세요."),

    # --- UNKNOWN (7) ---
    ("d160_unk_01", IssueType.UNKNOWN, "어제 앱을 쓰다가 뭔가 이상했는데 정확히 뭐였는지 기억이 안 납니다."),
    ("d160_unk_02", IssueType.UNKNOWN, "이용 중에 오류가 있었던 것 같은데 어떤 부분인지 설명하기 어렵습니다."),
    ("d160_unk_03", IssueType.UNKNOWN, "뭔가 제대로 안 됐는데 구체적으로 어디서 문제였는지 모르겠습니다."),
    ("d160_unk_04", IssueType.UNKNOWN, "잠깐 이상 증상이 있었는데 금방 사라져서 뭐라 말하기 애매합니다."),
    ("d160_unk_05", IssueType.UNKNOWN, "사용하다가 뭔가 걸리는 느낌이었는데 정확히는 모르겠습니다."),
    ("d160_unk_06", IssueType.UNKNOWN, "화면에 문제가 있었던 것 같은데 자세히는 기억이 안 납니다."),
    ("d160_unk_07", IssueType.UNKNOWN, "정상적으로 안 됐다는 것 외에는 뭐가 문제인지 모르겠습니다."),
]

assert len(CASES) == 80, f"dev set은 80건이어야 합니다 (현재 {len(CASES)}건)"

if __name__ == "__main__":
    from evaluate_issue_types_runner import run

    run(CASES, DATASET_VERSION)
