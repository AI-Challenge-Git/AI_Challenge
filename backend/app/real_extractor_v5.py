"""
실제 OpenAI LLM을 사용한 이중 구조화 구현.
FakeDualExtractor를 대체하되, 동일한 DualExtractor 프로토콜을 따른다.

버전: v6 (2026-08-19)
- 스키마 검증(AI-05: NEEDS_CONFIRMATION은 value=null, evidence는 선택)을
  코드에서 우회하지 않고, 위반 시 안전하게 FAILED로 처리 (AI-07)
- FE-07: 날짜 없는 시각을 LLM이 임의의 날짜와 결합해 CONFIRMED로 만드는 경우 차단
- order_type: 원문에 지정가/시장가가 전혀 없는데 모델이 LIMIT/MARKET을
  확정한 "순수 hallucination" 케이스만 deterministic fallback으로
  UNKNOWN으로 낮춘다 (LLM 재호출 없이). 원문에 실제 단서가 있는데
  잘못 분류했거나, evidence 자체를 조작한 경우는 fallback 대상이 아니며
  기존 correction retry로 넘어간다.
- action(매수/매도): OrderAction에 BUY 추가 반영, 프롬프트에 매수/매도
  분류 규칙 명시.
- correction retry를 1회 -> 최대 2회로 확장 (evidence_quote 재조합처럼
  모델이 확률적으로 실수하는 유형이 1회 재시도로는 복구가 안 되는
  경우가 실사용에서 관찰되어, 재시도 여지를 늘림).
- issue_type: 로컬 키워드 규칙(_classify_issue_type_candidate)이 값을
  직접 확정/덮어쓰던 동작을 제거. 이제 로컬 규칙은 "1차 확정값과 충돌하는가"
  (_has_issue_type_conflict)만 판단해 전용 AI 재분류 호출 여부를 결정하고,
  실제 issue_type 값과 evidence_quote는 항상 LLM(전체 추출 또는 전용 분류)
  응답에서만 가져온다. evidence_quote도 masked_text 전체가 아니라 전용
  분류 LLM이 직접 반환한 근거 조각을 substring 검증 후 사용한다.
"""

import json
import os
import re
import time
from collections.abc import Iterable
from enum import StrEnum
from typing import Any, NamedTuple, cast

from dotenv import load_dotenv
from openai import APITimeoutError, OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from app.codes import FieldStatus, IssueType, OrderAction, OrderType, SubmissionStatus
from app.schemas import ConsultationCandidate, ExtractionResult, TechnicalCandidate

load_dotenv()

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)


def _find_balanced_json_object(text: str) -> str | None:
    """
    text 안에서 첫 '{'부터 시작해, 문자열 리터럴 내부를 무시하고
    중괄호 깊이를 세어 정확히 대응되는 '}'까지의 JSON object를 찾는다.
    설명 문장 안에 있는 무관한 중괄호에 속지 않도록, 문자열 리터럴(큰따옴표)
    내부의 중괄호는 깊이 계산에서 제외한다.
    찾지 못하면 None을 반환한다.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _extract_json_text(raw_content: str) -> str:
    """
    LLM 응답에서 순수 JSON 텍스트만 추출한다.
    모델이 설명 문장이나 코드펜스로 JSON을 감싸는 경우를 방어한다.

    1. ```json ... ``` 코드블록이 있으면 그 안의 내용만 사용한다.
    2. 코드블록이 없고 응답 자체가 { 로 시작해 } 로 끝나면 그대로 사용한다.
    3. 위 두 경우가 아니면, 첫 '{'부터 중괄호 깊이를 세어 정확히 대응되는
       '}'까지를 JSON object로 추출한다 (문자열 리터럴 내부 중괄호는 무시).
    4. 셋 다 실패하면 JSON을 찾을 수 없다고 실패 처리한다.
    """
    text = raw_content.strip()

    fenced = _JSON_FENCE_PATTERN.search(text)
    if fenced:
        return fenced.group(1).strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    balanced = _find_balanced_json_object(text)
    if balanced is not None:
        return balanced

    raise ValueError("LLM 응답에서 JSON object를 찾을 수 없습니다.")


_ISSUE_TYPE_VALUES = [t.value for t in IssueType]
_SUBMISSION_STATUS_VALUES = [s.value for s in SubmissionStatus]
_ORDER_ACTION_VALUES = [a.value for a in OrderAction]
_ORDER_TYPE_VALUES = [t.value for t in OrderType]

# symptom canonicalization (evaluation_symptom_label, 2026-09-02 팀 리뷰 확정 초안).
# 자유 요약 대신 issue_type별 고정 카테고리 중 가장 가까운 것의 대표 문구를 그대로
# symptom.value로 쓰게 해서, 같은 의미의 제보끼리 embedding이 안정적으로 가까워지게
# 한다 (paraphrase로 인한 클러스터링 실패 완화). 공식 운영 표준이 아니라 평가/군집화
# 전용 라벨이며, DEVICE_NETWORK_SUSPECTED 세부 라벨은 원인을 확정하지 않고 고객이
# 보고한 환경·관찰 증상만 표현한다. 이 목록을 바꾸면 embedding 입력 분포가 바뀌므로
# 기존 벡터와 같은 policy/model_revision으로 섞지 않고 재평가 후 새 revision으로
# 등록해야 한다.
_SYMPTOM_TAXONOMY: dict[IssueType, list[tuple[str, str, str]]] = {
    IssueType.ORDER_SUBMISSION_FAILURE: [
        (
            "ORDER_UI_UNRESPONSIVE",
            "주문 화면이 멈춤",
            "주문·확인 버튼을 누른 뒤 로딩, 멈춤, 무응답 또는 다음 화면으로 넘어가지 않음",
        ),
    ],
    IssueType.ORDER_RESULT_UNCONFIRMED: [
        (
            "ORDER_STATUS_UNAVAILABLE",
            "주문 접수 여부를 확인할 수 없음",
            "주문이 접수됐는지, 거부됐는지, 대기 중인지 확인할 수 없음",
        ),
        (
            "EXECUTION_STATUS_UNAVAILABLE",
            "체결 여부를 확인할 수 없음",
            "체결·미체결·부분체결 여부를 확인할 수 없음",
        ),
    ],
    IssueType.LOGIN_ACCESS_FAILURE: [
        (
            "LOGIN_GENERIC_FAILURE",
            "로그인이 되지 않음",
            "로그인 실패는 확인되지만 구체적인 인증 원인은 불명확함",
        ),
        (
            "AUTHENTICATOR_FAILURE",
            "인증 수단 오류로 로그인 실패",
            "비밀번호, PIN, 인증서, 생체인증 등 인증 수단이 실패함",
        ),
        (
            "AUTH_CODE_DELIVERY_FAILURE",
            "인증번호가 오지 않음",
            "SMS·OTP·본인인증 번호가 도착하지 않음",
        ),
    ],
    IssueType.BALANCE_INQUIRY_ERROR: [
        (
            "BALANCE_DATA_STALE",
            "잔고가 갱신되지 않음",
            "잔고가 이전 값으로 남아 있거나 최신 상태로 갱신되지 않음",
        ),
        (
            "BALANCE_DATA_MISSING",
            "보유 종목·수량·금액 데이터가 누락됨",
            "보유 종목·수량·금액 등 데이터가 누락되거나 표시되지 않음",
        ),
        (
            "EXECUTION_HISTORY_EMPTY",
            "체결 내역이 비어 있음",
            "체결내역 화면에 거래 기록이 표시되지 않음",
        ),
        (
            "ORDER_HISTORY_LOAD_FAILURE",
            "주문 내역 조회에 실패함",
            "주문내역 화면 로딩 실패, 조회 오류 또는 빈 화면이 발생함",
        ),
    ],
    IssueType.DEVICE_NETWORK_SUSPECTED: [
        (
            "DEVICE_SPECIFIC_FAILURE",
            "특정 기기에서만 재현되는 오류",
            "다른 기기에서는 정상이나 특정 기기에서만 재현된다고 명시됨 (여러 조건에 "
            "동시에 해당하면 이것을 최우선으로 선택)",
        ),
        (
            "NETWORK_ERROR_MESSAGE",
            "네트워크 오류 메시지가 표시됨",
            "네트워크·통신·연결 오류 메시지가 명시적으로 표시됨 (기기 특정 조건이 "
            "없을 때 두 번째 우선순위)",
        ),
        (
            "WIFI_ASSOCIATED_FAILURE",
            "와이파이 연결 상태에서 발생하는 오류",
            "Wi-Fi 사용·끊김·불안정 상황과 함께 문제가 발생한다고 보고됨 (원인을 "
            "확정하는 것이 아니라 그 환경에서 발생했다는 관찰만 표현, 세 번째 우선순위)",
        ),
        (
            "CELLULAR_ASSOCIATED_FAILURE",
            "모바일 데이터 연결 상태에서 발생하는 오류",
            "LTE·5G·모바일 데이터 상황과 함께 문제가 발생한다고 보고됨 (네 번째 "
            "우선순위, 위 세 카테고리 중 아무것도 해당하지 않을 때만 선택)",
        ),
    ],
}


def _build_symptom_taxonomy_block() -> str:
    lines = []
    for issue_type, entries in _SYMPTOM_TAXONOMY.items():
        lines.append(f"### {issue_type.value}")
        for _label, phrase, criteria in entries:
            lines.append(f'- "{phrase}" : {criteria}')
    return "\n".join(lines)


_SYMPTOM_TAXONOMY_BLOCK = _build_symptom_taxonomy_block()

_SYSTEM_PROMPT = f"""당신은 증권사 MTS(모바일트레이딩시스템) 고객 제보를 분석하는 AI입니다.
고객이 작성한 자유서술 제보에서 정보를 추출하되, 아래 규칙을 반드시 지켜야 합니다.

## 절대 규칙
1. 원문(마스킹된 텍스트)에 없는 정보를 절대 추정·생성하지 마세요.
2. 근거가 없는 필드는 value=null, status="UNKNOWN", evidence_quote=null 로 처리하세요.
3. evidence_quote는 반드시 원문에 있는 문장을 그대로 복사(copy-paste)한 것이어야
   합니다. 요약, 어미 변경, 문법 수정, 의역을 절대 하지 마세요. 원문에 있는
   글자 그대로, 연속된 부분 문자열(substring)만 사용하세요.

   금지 예시 (절대 이렇게 바꾸지 마세요):
   원문에 "비밀번호가 틀렸다고 나와요"가 있는데
   evidence_quote = "비밀번호가 틀렸다고 나옴" ← 어미를 "나와요"에서 "나옴"으로
   바꿈. 이것은 금지된 변형입니다.

   올바른 예시:
   원문에 "비밀번호가 틀렸다고 나와요"가 있으면
   evidence_quote = "비밀번호가 틀렸다고 나와요" (원문 그대로, 한 글자도 다르지 않게)
4. [PHONE], [ACCOUNT], [EMAIL] 같은 마스킹 표시가 보이면 그 자체를 그대로 두세요.
   실제 전화번호나 계좌번호가 무엇인지 추측하지 마세요.
5. 기술 증상(technical)에는 종목명, 수량, 가격 등 개인 매매정보를 절대 포함하지 마세요.
   그 정보는 반드시 consultation 쪽에만 넣으세요.
6. quantity와 price_krw는 반드시 순수 숫자만 넣으세요 (예: "10주"가 아니라 10,
   "70,000원"이 아니라 70000). 단위나 콤마, 원화 기호를 포함하지 마세요.
7. symptom 값은 아래 issue_type별 고정 카테고리 목록에서 고르세요.
   ★★★ 반드시 당신이 이번 응답의 issue_type으로 결정한 값과 정확히 같은
   "### issue_type이름" 섹션 안에서만 골라야 합니다. 다른 issue_type 섹션의
   문구를 절대 가져오지 마세요 — 예를 들어 issue_type=LOGIN_ACCESS_FAILURE로
   판단했다면 symptom도 반드시 "### LOGIN_ACCESS_FAILURE" 섹션 안의 문구
   중에서만 골라야 하고, "### BALANCE_INQUIRY_ERROR" 섹션의 문구("잔고가
   갱신되지 않음" 등)를 쓰면 안 됩니다. ★★★
   그 섹션 안에서 가장 가까운 카테고리를 찾아, 대표 문구(따옴표 안 텍스트)를
   한 글자도 바꾸지 말고 그대로 symptom.value에 넣으세요. 같은 의미의
   제보끼리 항상 같은 문구를 쓰게 해서 유사도 비교가 안정적으로 되게 하려는
   목적입니다. 그 섹션 안 어느 것과도 명확히 안 맞으면, 다른 섹션에서
   억지로 끼워맞추지 말고 기존 방식대로 고객이 겪은 관찰 가능한 현상을
   간결하게 요약하세요(예: "로딩이 멈춤"). 원문 문장 전체를 그대로 복사하거나,
   원인을 추측해서 서술하지 마세요.
   evidence_quote는 (대표 문구가 아니라) 원문에서 그 판단의 근거가 된 부분만
   그대로 인용하세요 — evidence_quote는 계속 masked_text의 정확한 substring이어야
   합니다.

## symptom 고정 카테고리 목록 (evaluation_symptom_label, 평가·군집화 전용)
공식 금융 장애 표준이나 운영 장애 확정 코드가 아니며, 원인을 확정하지 않습니다.
목록에 없는 issue_type(UNRELATED_OR_AMBIGUOUS, UNKNOWN)은 이 규칙과 무관합니다.
{_SYMPTOM_TAXONOMY_BLOCK}

## action(매수/매도) 규칙
action은 "매수", "샀다", "매수 주문" 등 명확한 매수 표현이 있으면 BUY,
"매도", "팔았다", "매도 주문" 등 명확한 매도 표현이 있으면 SELL로 분류하세요.
어느 쪽인지 원문에 명시적 표현이 없으면 반드시 UNKNOWN입니다.

## order_type 절대 규칙 (반드시 지킬 것)
- 원문에 "지정가"라는 단어가 정확히 존재할 때만:
  value = "LIMIT", status = "CONFIRMED_FROM_TEXT"
- 원문에 "시장가"라는 단어가 정확히 존재할 때만:
  value = "MARKET", status = "CONFIRMED_FROM_TEXT"
- "지정가", "시장가" 둘 다 원문에 없다면 반드시:
  value = null, status = "UNKNOWN", evidence_quote = null
- "매도", "매도 주문", "주문을 넣었다", 가격 언급만으로는 LIMIT 또는 MARKET을
  추론하지 마세요. 이런 표현만으로는 주문 방식을 알 수 없습니다.

중요: status가 "UNKNOWN"이면 value에 문자열 "UNKNOWN"을 넣지 마세요.
반드시 JSON null을 사용하세요. "UNKNOWN"은 status 필드의 값이지 value가 아닙니다.

예시:
입력: "삼성전자 매도 주문을 넣었는데 로딩돼요."
정답: {{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

입력: "삼성전자 지정가 매도 주문을 넣었다"
정답: {{"value": "LIMIT", "status": "CONFIRMED_FROM_TEXT", "evidence_quote": "지정가"}}

evidence_quote를 채울 때는 원문 문장을 절대 바꾸지 말고 정확히 그대로 복사하세요.
"넣었는데"를 "넣었다"로 바꾸는 것처럼 어미를 조금이라도 수정하면 안 됩니다.

## 필드 상태(status) 규칙 - 매우 중요, 반드시 지켜야 합니다
- "CONFIRMED_FROM_TEXT": 원문에서 명확하게 확인 가능한 값. 이 경우에만
  value와 evidence_quote를 채우세요.
- "NEEDS_CONFIRMATION": 원문에 관련 단서는 있지만 값을 확정할 수 없어
  고객 확인이 필요한 경우입니다.
  ★★★ value는 반드시 null이어야 합니다. 절대로 짐작한 값을 넣지 마세요. ★★★
  원문에 판단 근거가 있다면 evidence_quote에는 해당 원문 표현을 그대로
  넣을 수 있습니다. 근거가 없다면 evidence_quote도 null로 처리하세요.
- "UNKNOWN": 원문에 정보가 없음. value=null, evidence_quote=null.
- "OUT_OF_SCOPE": 이 서비스가 다루는 범위 밖의 정보. value=null, evidence_quote=null.

## 예시
원문: "10시 30분쯤 주문했어요" (날짜 없이 시각만 언급됨)
reported_occurred_at:
{{"value": null, "status": "NEEDS_CONFIRMATION", "evidence_quote": "10시 30분쯤"}}

원문에 주문 방식(지정가/시장가)이 전혀 언급되지 않음:
order_type:
{{"value": null, "status": "NEEDS_CONFIRMATION", "evidence_quote": null}}

## UNKNOWN과 NEEDS_CONFIRMATION 구분 (중요)
- "UNKNOWN": 해당 필드와 관련된 정보나 단서가 원문에 전혀 없는 경우.
  "추가 확인이 필요하다"는 이유만으로 NEEDS_CONFIRMATION을 사용하지 마세요.
- "NEEDS_CONFIRMATION": 해당 필드와 관련된 단서가 원문에 존재하지만,
  그 단서만으로 확정된 value를 생성할 수 없는 경우. value는 반드시 null입니다.

예시: 로그인 실패 문의처럼 주문 자체가 언급되지 않은 경우, quantity/order_type/
price_krw/action 등 주문 관련 필드는 관련 단서 자체가 없으므로 반드시 "UNKNOWN"을
사용하세요. "나중에 물어보면 알 수 있다"는 이유로 이런 필드를 NEEDS_CONFIRMATION으로
표시하지 마세요.

예시:
"10시 30분쯤 주문했다" → attempted_at = NEEDS_CONFIRMATION
(시각 단서는 있지만 완전한 날짜 정보가 없음)
시간 관련 표현 자체가 없음 → attempted_at = UNKNOWN

## submission_status 규칙 (중요)
submission_status는 오직 고객이 말한 "주식 주문 제출 여부"만 나타냅니다.
- 고객이 주문을 넣었다/제출했다는 사실을 명시한 경우: CUSTOMER_REPORTED_SUBMITTED
- 고객이 주문이 제출되지 않았다고 명시한 경우: CUSTOMER_REPORTED_NOT_SUBMITTED
- 주문 제출 여부를 판단할 직접적인 표현이 없는 경우: status="UNKNOWN"

로그인 시도, 앱 실행, 화면 이동, 버튼 클릭 등 다른 행위를 주문 제출로 해석하지 마세요.

★★★ 매우 중요: "UNKNOWN"은 status 필드에 들어가는 값이지, value 필드에
들어가는 값이 아닙니다. 정보가 없으면 value는 반드시 null(값 없음)이어야
합니다. value 필드에 문자열 "UNKNOWN"을 넣으면 안 됩니다. ★★★

다음은 잘못된 출력입니다 (절대 이렇게 쓰지 마세요):
{{"value": "UNKNOWN", "status": "UNKNOWN", "evidence_quote": null}}

다음이 올바른 출력입니다:
{{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

예시:
"MTS가 이상해요."
→ {{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

"로그인하려는데 비밀번호가 틀렸다고 나와요."
→ {{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

"매도 주문을 넣었는데 계속 로딩됩니다."
→ {{"value": "CUSTOMER_REPORTED_SUBMITTED", "status": "CONFIRMED_FROM_TEXT",
   "evidence_quote": "매도 주문을 넣었는데"}}

## MVP 범위 관련 주의
원문에 나타나지 않은 주문 정보를 다른 행동으로부터 추론하지 마세요.
로그인, 앱 실행, 이체, 시세 조회 등의 행동을 매수·매도 주문이나 주문 제출
행위로 해석하지 마세요. IssueType은 현재 정의된 taxonomy에 따라 분류하되,
주문 관련 필드(submission_status, action, order_type, attempted_at 등)는
실제 주문 관련 근거가 있는 경우에만 추출하세요.

attempted_at은 오직 주식 주문을 시도한 시각만을 의미합니다.
로그인, 앱 실행, 이체, 조회 등 주문과 무관한 다른 행동의 시각을
attempted_at으로 사용하지 마세요. 그런 경우 attempted_at은 반드시 UNKNOWN이며
value와 evidence_quote 모두 null이어야 합니다. 원문 전체를 evidence_quote로
넣는 것으로 이 규칙을 우회하지 마세요.
(reported_occurred_at은 장애가 발생한 시각이므로, 로그인 장애처럼 주문과
무관한 문의에서도 발생 시각 단서가 있다면 사용할 수 있습니다.)

## issue_type 분류 기준
문장이 짧거나 종목·수량 정보가 없다는 이유만으로 UNKNOWN 또는
UNRELATED_OR_AMBIGUOUS로 후퇴하지 마세요. 아래에서 관찰된 장애 단계나
명시적인 원인 단서에 맞는 유형을 선택하세요.

### issue_type 필드 출력 계약
IssueType과 FieldStatus는 서로 다른 값입니다.

분류 가능한 경우 IssueType 문자열은 반드시 value에 넣고 status는 반드시
CONFIRMED_FROM_TEXT로 작성하세요. evidence_quote는 분류 근거가 되는 원문의
정확한 부분 문자열이어야 합니다.

올바른 분류 성공 형식:
{{"value": "ORDER_SUBMISSION_FAILURE", "status": "CONFIRMED_FROM_TEXT",
  "evidence_quote": "주문 버튼을 눌렀는데 화면이 멈췄습니다."}}

잘못된 형식 - IssueType을 status에 넣지 마세요:
{{"value": null, "status": "ORDER_SUBMISSION_FAILURE",
  "evidence_quote": "주문 버튼을 눌렀는데 화면이 멈췄습니다."}}

분류할 근거가 부족한 경우에만 다음 UNKNOWN 형식을 사용하세요:
{{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

status에 허용되는 값은 CONFIRMED_FROM_TEXT, NEEDS_CONFIRMATION, UNKNOWN,
OUT_OF_SCOPE뿐입니다. ORDER_SUBMISSION_FAILURE 같은 IssueType 문자열은
status 값이 될 수 없습니다.

### ORDER_SUBMISSION_FAILURE
주문 버튼, 매수·매도 확인, 주문 제출 등 주문을 보내는 단계에서 화면 멈춤,
무반응, 다음 화면으로 넘어가지 않음, 계속 로딩되는 증상입니다.
- 예: "주문 버튼을 눌렀는데 화면이 멈췄습니다."
- 예: "매도 확인 후 다음 단계로 넘어가지 않습니다."
- 예: "주문 제출 중 계속 로딩됩니다."

### ORDER_RESULT_UNCONFIRMED
주문을 시도한 뒤 접수 여부, 주문번호, 처리 결과 또는 체결 여부를 확인할 수
없는 증상입니다. 주문을 보내는 도중 멈춘 것과 구분하세요.
- 체결·주문 내역 화면이 언급돼도, 일반적인 과거 내역 조회가 아니라 방금 보낸
  특정 주문이 존재하는지 또는 접수됐는지 불확실한 것이 핵심이면 이 유형입니다.
- 예: "주문했는데 접수됐는지 모르겠습니다."
- 예: "주문번호가 표시되지 않아 결과를 확인할 수 없습니다."
- 예: "매도 후 체결 여부를 확인할 수 없습니다."
- 예: "체결 내역에서 방금 보낸 주문이 있는지 없는지 안 보입니다."

### LOGIN_ACCESS_FAILURE
로그인·비밀번호·본인인증·인증번호와 관련된 명시적인 접속 실패입니다.
- 예: "비밀번호가 틀렸다고 나와 로그인되지 않습니다."
- 예: "인증번호가 오지 않아 로그인할 수 없습니다."
이 경우 submission_status/action/order_type/attempted_at 등 주문 관련 필드는
로그인 문의와 무관하므로 전부 UNKNOWN으로 두세요.

### BALANCE_INQUIRY_ERROR
잔고, 보유 종목·수량, 체결 내역 또는 주문 내역의 조회·표시·갱신 오류입니다.
다만 방금 시도한 특정 주문의 존재·접수·처리 여부가 불확실한 경우는
ORDER_RESULT_UNCONFIRMED를 우선합니다.
- 예: "잔고 화면이 갱신되지 않습니다."
- 예: "보유 주식 수량이 표시되지 않습니다."
- 예: "체결 내역을 조회하면 빈 화면이 나옵니다."

### DEVICE_NETWORK_SUSPECTED
와이파이, 모바일 데이터, 통신 연결 또는 특정 휴대전화·기기와 장애의 연관성이
원문에 명시된 경우입니다. 일반적인 화면 멈춤만으로 네트워크나 기기 문제를
추론하지 마세요. 명시적인 네트워크·기기 조건이 있으면 다른 기능별 유형보다
DEVICE_NETWORK_SUSPECTED를 우선합니다.
- 예: "와이파이가 끊길 때 앱이 멈춥니다."
- 예: "다른 휴대전화에서는 되는데 제 기기에서만 실행되지 않습니다."

### UNRELATED_OR_AMBIGUOUS
장애 제보가 아니라 수수료, 일정, 전망, 사용법 등을 묻는 정보성 문의이거나
현재 장애 taxonomy와 무관한 요청입니다.
- 예: "해외주식 거래 수수료가 궁금합니다."
- 예: "공모주 청약 일정을 알려주세요."
- 예: "오늘 주가 전망이 궁금합니다."
- 예: "보유 중인 종목이 있어서 그런데, 배당금 지급일이 언제인지 확인하고 싶어요."
질문 앞에 상황이나 이유를 설명하는 구절이 붙어도, 핵심 요청이 정보성 질문이면
UNRELATED_OR_AMBIGUOUS입니다. 이유 설명 구절 자체를 장애 근거로 오인해
UNKNOWN으로 후퇴하지 마세요.

### UNKNOWN
장애가 발생했다는 표현은 있지만 어느 기능이나 원인인지 분류할 근거가
부족한 경우입니다. 정보성 문의인 UNRELATED_OR_AMBIGUOUS와 구분하세요.
- 예: "M-able에서 문제가 발생했습니다."
- 예: "기능이 제대로 되지 않습니다."
UNKNOWN은 enum 문자열을 value에 넣지 말고 반드시 다음처럼 출력하세요.
{{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

우선순위:
1. 명시적인 네트워크·기기 조건이 있으면 DEVICE_NETWORK_SUSPECTED
2. 그 외에는 장애가 발생한 기능과 단계에 따라 주문 제출, 주문 결과, 로그인,
   잔고·내역 조회 유형을 선택
3. 장애가 아닌 정보성 문의는 UNRELATED_OR_AMBIGUOUS
4. 장애는 명시됐지만 분류 근거가 부족할 때만 UNKNOWN

## 다시 한번 강조합니다
status가 "CONFIRMED_FROM_TEXT"일 때만 value가 non-null일 수 있습니다.
그 외 모든 status(NEEDS_CONFIRMATION, UNKNOWN, OUT_OF_SCOPE)에서는
value가 반드시 null이어야 합니다. 이 규칙을 어기면 응답 전체가 거부됩니다.

## 날짜·시각(reported_occurred_at, attempted_at) 처리 규칙

날짜·시각 필드는 원문에 실제로 명시된 정보만 사용하세요.

### 완전한 날짜·시각

원문에 다음 정보가 모두 있으면 완전한 날짜·시각입니다.

- 4자리 또는 2자리 연도
- 월
- 일
- 시각

완전한 날짜·시각은 반드시 status="CONFIRMED_FROM_TEXT"로 처리하세요.
완전한 날짜·시각을 status="NEEDS_CONFIRMATION"으로 낮추면 안 됩니다.

value는 반드시 UTC offset을 포함한 ISO 8601 형식으로 작성하세요.
한국어 원문에 별도의 시간대가 명시되지 않은 경우 이 서비스의 시연 범위인
Asia/Seoul의 UTC offset인 "+09:00"을 사용하세요.

### 4자리 연도

원문에 4자리 연도·월·일·시각이 모두 있으면 그대로 ISO 8601 형식으로
정규화하세요.

원문:
"2026년 8월 15일 오전 9시 3분에 매도 주문을 넣었다"

reported_occurred_at:
{{"value": "2026-08-15T09:03:00+09:00",
  "status": "CONFIRMED_FROM_TEXT",
  "evidence_quote": "2026년 8월 15일 오전 9시 3분에"}}

attempted_at:
{{"value": "2026-08-15T09:03:00+09:00",
  "status": "CONFIRMED_FROM_TEXT",
  "evidence_quote": "2026년 8월 15일 오전 9시 3분에"}}

다음과 같이 완전한 날짜·시각을 NEEDS_CONFIRMATION으로 처리하면 안 됩니다.

잘못된 출력:
{{"value": null,
  "status": "NEEDS_CONFIRMATION",
  "evidence_quote": "2026년 8월 15일 오전 9시 3분에"}}

### 2자리 축약 연도

원문에서 연도가 정확히 두 자리로 표현된 경우 YY년을 20YY년으로
정규화하세요.

예:
- 26년 → 2026년
- 25년 → 2025년

두 자리 연도·월·일·시각이 모두 있으면 완전한 날짜·시각입니다.
반드시 status="CONFIRMED_FROM_TEXT"를 사용하세요.

value에는 정규화된 4자리 연도를 사용하세요.
evidence_quote의 연도는 변환하지 말고 원문의 두 자리 연도 표현을
그대로 복사하세요.

원문:
"26년 8월 15일 오후 8시 25분에 매도 주문을 넣었다"

reported_occurred_at:
{{"value": "2026-08-15T20:25:00+09:00",
  "status": "CONFIRMED_FROM_TEXT",
  "evidence_quote": "26년 8월 15일 오후 8시 25분에"}}

attempted_at:
{{"value": "2026-08-15T20:25:00+09:00",
  "status": "CONFIRMED_FROM_TEXT",
  "evidence_quote": "26년 8월 15일 오후 8시 25분에"}}

다음과 같이 evidence_quote의 연도를 임의로 바꾸면 안 됩니다.

잘못된 evidence_quote:
"2026년 8월 15일 오후 8시 25분에"

올바른 evidence_quote:
"26년 8월 15일 오후 8시 25분에"

### 날짜가 없는 시각

원문에 시각만 있고 연도·월·일이 모두 존재하지 않으면 날짜를 생성하지 마세요.

날짜 없는 시각은 반드시 다음과 같이 처리하세요.

- value=null
- status="NEEDS_CONFIRMATION"
- evidence_quote에는 원문의 시각 표현을 그대로 사용

원문:
"오전 9시 3분에 주문했다"

reported_occurred_at:
{{"value": null,
  "status": "NEEDS_CONFIRMATION",
  "evidence_quote": "오전 9시 3분에"}}

attempted_at:
{{"value": null,
  "status": "NEEDS_CONFIRMATION",
  "evidence_quote": "오전 9시 3분에"}}

날짜가 없는 시각에 오늘 날짜나 다른 임의의 날짜를 결합하면 안 됩니다.

### 날짜·시각 정보가 전혀 없는 경우

원문에 날짜나 시각 관련 표현이 전혀 없으면 다음과 같이 처리하세요.

{{"value": null,
  "status": "UNKNOWN",
  "evidence_quote": null}}

### 필드 의미 구분

reported_occurred_at은 고객이 장애를 겪은 발생 시각입니다.
attempted_at은 고객이 주식 주문을 시도한 시각입니다.

동일한 원문 날짜·시각 표현이 장애 발생과 주문 시도를 모두 직접 나타낼 때만
두 필드에 같은 값을 사용할 수 있습니다.

로그인, 앱 실행, 이체, 시세 조회 등 주문과 무관한 행동의 시각을
attempted_at으로 사용하면 안 됩니다.

## 필수 JSON 필드 규칙 - 매우 중요
아래 필드는 정보 존재 여부와 관계없이 절대 생략하지 마세요.

technical: issue_type, symptom, submission_status, error_code, reported_occurred_at
consultation: action, symbol_name, symbol_code, quantity, order_type, price_krw, attempted_at

원문에 정보가 없는 경우에도 해당 필드를 삭제하지 말고 반드시 다음 형태로 출력하세요.
{{"value": null, "status": "UNKNOWN", "evidence_quote": null}}

JSON의 technical 5개 필드와 consultation 7개 필드를 예외 없이 항상 전부 출력해야 합니다.
하나라도 빠지면 응답 전체가 거부됩니다.

## issue_type 허용값 (이 중 하나만 사용)
{_ISSUE_TYPE_VALUES}

## submission_status 허용값
{_SUBMISSION_STATUS_VALUES}

## action 허용값 (매매 방향)
{_ORDER_ACTION_VALUES}

## order_type 허용값
{_ORDER_TYPE_VALUES}

## 출력 형식
반드시 아래 JSON 스키마 형태로만 응답하세요.
설명, 분석 문장, 코드블록 표시(```), JSON 앞뒤 문장을 절대 출력하지 마세요.
응답의 첫 문자는 {{ 이어야 하고 마지막 문자는 }} 이어야 합니다.

{{
  "technical": {{
    "issue_type": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "symptom": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "submission_status": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "error_code": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "reported_occurred_at": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}}
  }},
  "consultation": {{
    "action": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "symbol_name": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "symbol_code": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "quantity": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "order_type": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "price_krw": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}},
    "attempted_at": {{"value": "...", "status": "...", "evidence_quote": "..." 또는 null}}
  }}
}}
"""


class OrderTypeSemanticError(ValueError):
    """order_type의 근거 없는 semantic 확정을 나타내는 전용 예외."""


class ExtractFailureReason(StrEnum):
    """AI-07: timeout / invalid_schema / provider_unavailable 분리"""

    TIMEOUT = "TIMEOUT"
    INVALID_JSON = "INVALID_JSON"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class ExtractOutcome(NamedTuple):
    """
    extract_safe()의 반환 타입. result와 failure_reason 중 하나만 채워진다.

    attempt_count: 전체 구조화 추출 LLM의 호출 횟수 (1~3)
    first_pass_valid: 1차 응답이 재요청(LLM 재호출) 없이 최종 성공했는지 여부.
        order_type deterministic fallback이 적용된 경우도 LLM은 1번만
        호출됐으므로 first_pass_valid=True로 취급한다
        (semantic_fallback_applied로 구분).
    semantic_fallback_applied: order_type 등 deterministic fallback이
        적용되어 값이 downgrade됐는지 여부. 평가 시 "모델 원본 그대로 통과"와
        "로컬 안전 보정으로 통과"를 구분하기 위한 필드.
    failure_reason / detail: 최종(마지막 시도) 실패 정보
    first_failure_reason / first_failure_detail: 1차 시도가 실패했을 경우
        그 실패 정보. 1차가 바로 성공했다면 둘 다 None.
    classification_call_count: issue_type 전용 분류 호출 횟수.
    classification_override_applied: 전용 분류가 기존 issue_type을 바꿨는지 여부.
    """

    result: ExtractionResult | None
    failure_reason: ExtractFailureReason | None
    detail: str | None = None
    attempt_count: int = 1
    first_pass_valid: bool = True
    semantic_fallback_applied: bool = False
    first_failure_reason: ExtractFailureReason | None = None
    first_failure_detail: str | None = None
    classification_call_count: int = 0
    classification_override_applied: bool = False


class RealDualExtractor:
    """OpenAI API를 사용해 실제로 이중 구조화를 수행하는 extractor."""

    def __init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(".env에 OPENAI_API_KEY가 설정되어 있지 않습니다.")

        self._client = OpenAI(
            api_key=api_key,
            timeout=90.0,
            max_retries=1,
        )
        self._model = "gpt-4.1-mini"

    def _coerce_types(self, field_dict: dict[str, Any], value_type: type | None) -> dict[str, Any]:
        """
        문자열로 온 값을 실제 타입(Enum, int)으로 바꾸는 타입 변환만 수행한다.
        규칙 위반(status/value 불일치) 여부는 여기서 손대지 않고
        Pydantic 검증(model_validate)에 그대로 맡긴다 - 위반을 코드로
        조용히 고쳐버리면 실제 모델의 규칙 준수율을 왜곡해서 평가하게 되므로,
        위반은 위반대로 드러나서 실패 처리되도록 둔다.

        예외적으로 딱 한 가지 케이스만 여기서 정규화한다:
        status가 "UNKNOWN"인데 value에 (null이 아니라) 문자열 "UNKNOWN"이
        그대로 들어온 경우. 이건 order_type의 LIMIT/MARKET hallucination과
        성격이 다르다 - 모델이 이미 "정보 없음"이라는 올바른 결론에 도달했고,
        단지 그것을 표현하는 방식(JSON null 대신 문자열 "UNKNOWN")만 스키마
        규칙과 다르게 쓴 순수 형식 오류다.
        """
        if not isinstance(field_dict, dict):
            # 모델이 {value, status, evidence_quote} 객체 대신 문자열 등
            # 다른 형태를 반환한 경우. AttributeError로 죽지 않고 AI-07이
            # 요구하는 INVALID_SCHEMA 실패 경로(correction retry)로 보낸다.
            raise ValueError(
                f"필드가 {{value, status, evidence_quote}} 객체가 아닙니다: {field_dict!r}"
            )
        if field_dict.get("status") is not None:
            field_dict["status"] = FieldStatus(field_dict["status"])

        if field_dict["status"] == FieldStatus.UNKNOWN and field_dict.get("value") == "UNKNOWN":
            field_dict["value"] = None

        if value_type is not None and field_dict.get("value") is not None:
            raw_value = field_dict["value"]
            if value_type is int:
                if isinstance(raw_value, str):
                    digits_only = re.sub(r"[^\d-]", "", raw_value)
                    if not digits_only:
                        raise ValueError(f"숫자로 변환할 수 없는 값입니다: {raw_value!r}")
                    field_dict["value"] = int(digits_only)
                else:
                    field_dict["value"] = int(raw_value)
            else:
                field_dict["value"] = value_type(raw_value)
        return field_dict

    def _normalize_unknown_fields(self, parsed: dict[str, Any]) -> bool:
        """UNKNOWN 결론은 유지하고 value/evidence의 표현만 계약에 맞춘다."""
        changed = False
        for section_name in ("technical", "consultation"):
            section = parsed.get(section_name)
            if not isinstance(section, dict):
                continue
            for field in section.values():
                if not isinstance(field, dict):
                    continue
                if field.get("status") != FieldStatus.UNKNOWN.value:
                    continue
                if field.get("value") not in (None, "UNKNOWN"):
                    continue
                if field.get("value") is not None:
                    field["value"] = None
                    changed = True
                if field.get("evidence_quote") is not None:
                    field["evidence_quote"] = None
                    changed = True
        return changed

    def _classify_issue_type_candidate(self, text: str) -> IssueType | None:
        """
        키워드 단서 기반 후보 탐지기.

        3차 회의 문서 12장의 역할 분담표에서 "오류 유형 분류"는 AI 담당으로
        명시되어 있으므로, 이 함수는 issue_type 값을 직접 확정하지 않는다.
        오직 _has_issue_type_conflict()에서 "전용 AI 재분류를 호출해야 하는
        상황인가"를 판단하는 신호로만 사용한다. 최종 확정은 항상
        _classify_issue_type_focused()(전용 LLM 호출)가 담당한다.
        """
        failure_terms = (
            "안 됨",
            "안됨",
            "안 되",
            "안 돼",
            "안 됩",
            "않",
            "못",
            "오류",
            "실패",
            "멈",
            "로딩",
            "무반응",
            "빈 화면",
            "표시되지",
            "나오지",
            "틀렸",
            "오지 않",
            "거부",
            "끊",
            "계속 돌아",
            "바뀌지",
            "되지",
            "먹통",
            "아무 반응",
            "빙글빙글",
            "거절돼",
            "되돌아",
            "돌아옵",
            "나타나지",
            "0으로 표시",
        )
        has_failure = any(term in text for term in failure_terms)

        information_topics = (
            "수수료",
            "비용",
            "방법",
            "절차",
            "순서",
            "일정",
            "예정 종목",
            "등록",
            "설치",
            "이전",
        )
        information_requests = (
            "궁금",
            "알려",
            "설명해",
            "얼마",
            "보고 싶",
            "확인하고 싶",
            "알고 싶",
        )
        if (
            not has_failure
            and any(term in text for term in information_topics)
            and any(term in text for term in information_requests)
        ):
            return IssueType.UNRELATED_OR_AMBIGUOUS

        network_terms = (
            "와이파이",
            "Wi-Fi",
            "wifi",
            "모바일 데이터",
            "LTE",
            "5G",
            "네트워크",
            "통신 연결",
            "무선망",
        )
        device_terms = (
            "다른 휴대전화",
            "다른 스마트폰",
            "다른 기기",
            "제 기기에서만",
            "내 기기에서만",
            "제 휴대전화에서만",
            "내 휴대전화에서만",
            "이 휴대폰에서만",
            "이 스마트폰에서만",
            "이 기기에서만",
            "제 스마트폰에서만",
            "제 휴대폰에서만",
            "다른 폰",
            "현재 단말에서만",
            "해당 단말에서만",
        )
        if (
            has_failure
            and not self._has_network_negation(text)
            and (
                any(term in text for term in network_terms)
                or any(term in text for term in device_terms)
            )
        ):
            return IssueType.DEVICE_NETWORK_SUSPECTED

        login_terms = (
            "로그인",
            "비밀번호",
            "인증번호",
            "본인인증",
            "계정 접속",
            "계정",
            "아이디로 접속",
        )
        if has_failure and any(term in text for term in login_terms):
            return IssueType.LOGIN_ACCESS_FAILURE

        result_terms = (
            "접수됐는지",
            "접수되었는지",
            "접수 여부",
            "주문번호",
            "체결 여부",
            "처리 결과",
            "주문 결과",
            "들어갔는지",
            "접수 번호",
            "체결된 건지",
            "미체결",
            "실제 주문",
            "방금 보낸 주문",
            "주문이 있는지",
            "있는지 없는지",
        )
        if any(term in text for term in result_terms) and (
            has_failure or "모르" in text or "확인" in text
        ):
            return IssueType.ORDER_RESULT_UNCONFIRMED

        balance_terms = (
            "잔고",
            "보유 주식",
            "보유 종목",
            "체결 내역",
            "체결내역",
            "주문 내역",
            "주문내역",
            "계좌 잔액",
            "보유 목록",
            "체결 기록",
            "예수금",
            "예탁 자산",
            "예탁자산",
            "보유 수량",
            "체결 건",
            "주문 기록",
        )
        if has_failure and any(term in text for term in balance_terms):
            return IssueType.BALANCE_INQUIRY_ERROR

        order_terms = ("주문", "매수", "매도")
        submission_terms = (
            "버튼",
            "확인 후",
            "제출",
            "주문을 넣",
            "전송 화면",
            "진행 표시",
            "확정",
            "확인창",
        )
        if (
            has_failure
            and any(term in text for term in order_terms)
            and any(term in text for term in submission_terms)
        ):
            return IssueType.ORDER_SUBMISSION_FAILURE

        information_terms = (
            "수수료",
            "청약 일정",
            "공모주",
            "주가 전망",
            "증시 전망",
            "사용법",
            "변경 방법",
            "방법을 알려",
            "절차를 알려",
            "거래 비용",
            "상장 종목 일정",
            "설치하는 방법",
        )
        if any(term in text for term in information_terms) and not has_failure:
            return IssueType.UNRELATED_OR_AMBIGUOUS

        return None

    def _has_network_negation(self, text: str) -> bool:
        """네트워크가 원인이 아니라고 명시한 표현인지 확인한다."""
        network_normal_terms = (
            "와이파이는 정상",
            "와이파이가 정상",
            "네트워크는 정상",
            "네트워크가 정상",
            "통신은 정상",
            "통신 상태는 정상",
            "연결은 정상",
            "데이터 연결은 정상",
            "인터넷은 멀쩡",
        )
        if any(term in text for term in network_normal_terms):
            return True

        network_negation_pattern = re.compile(
            r"(?:와이파이|네트워크|통신|데이터\s*연결|인터넷)"
            r"[^.!?\n]{0,20}"
            r"(?:정상|멀쩡|문제[^.!?\n]{0,5}없|오류[^.!?\n]{0,8}없|"
            r"이상[^.!?\n]{0,5}없)"
        )
        return network_negation_pattern.search(text) is not None

    def _issue_type_has_minimum_evidence(
        self,
        issue_type: IssueType,
        text: str,
    ) -> bool:
        """전용 LLM 후보가 원문의 최소 유형 단서를 갖는지 검증한다."""
        evidence_terms = {
            IssueType.ORDER_SUBMISSION_FAILURE: (
                "주문",
                "매수",
                "매도",
                "확정",
                "전송",
            ),
            IssueType.ORDER_RESULT_UNCONFIRMED: (
                "주문",
                "접수",
                "주문번호",
                "체결",
                "미체결",
            ),
            IssueType.LOGIN_ACCESS_FAILURE: (
                "로그인",
                "비밀번호",
                "인증",
                "계정",
                "아이디",
            ),
            IssueType.BALANCE_INQUIRY_ERROR: (
                "잔고",
                "예수금",
                "보유",
                "체결",
                "주문 기록",
                "주문 내역",
            ),
            IssueType.DEVICE_NETWORK_SUSPECTED: (
                "와이파이",
                "Wi-Fi",
                "wifi",
                "LTE",
                "5G",
                "네트워크",
                "통신",
                "휴대폰에서만",
                "스마트폰에서만",
                "기기에서만",
            ),
            IssueType.UNRELATED_OR_AMBIGUOUS: (
                "수수료",
                "비용",
                "일정",
                "방법",
                "절차",
                "전망",
                "사용법",
            ),
        }
        if issue_type is IssueType.UNKNOWN:
            return True
        terms = evidence_terms.get(issue_type, ())
        if not any(term in text for term in terms):
            return False
        if issue_type is IssueType.DEVICE_NETWORK_SUSPECTED and self._has_network_negation(text):
            return False
        return True

    def _has_issue_type_conflict(
        self,
        masked_text: str,
        result: ExtractionResult,
    ) -> bool:
        """
        1차 확정된 issue_type이 로컬 키워드 단서와 어긋나는지만 판단한다.

        값을 직접 바꾸지 않는다 — 충돌이 있으면 _apply_focused_issue_type()이
        전용 AI 재분류를 강제로 호출하도록 신호를 보낼 뿐이다. 전용 AI 호출이
        실패하거나 모호하면 기존 확정값이 그대로 유지된다 (AI-05: 근거 없는
        값을 임의로 확정/변경하지 않는다).
        """
        issue_type = result.technical.issue_type
        if issue_type.status is not FieldStatus.CONFIRMED_FROM_TEXT:
            return False

        candidate = self._classify_issue_type_candidate(masked_text)
        if candidate is not None and candidate is not issue_type.value:
            return True

        if issue_type.value is IssueType.DEVICE_NETWORK_SUSPECTED and self._has_network_negation(
            masked_text
        ):
            return True

        if issue_type.value is IssueType.ORDER_RESULT_UNCONFIRMED:
            submission_stall_terms = (
                "확인 후 다음 단계로 넘어가지",
                "확인 후 다음 화면으로 넘어가지",
                "주문 화면이 반응하지",
                "주문 버튼을 눌러도 반응하지",
            )
            result_terms = (
                "접수됐는지",
                "접수되었는지",
                "접수 여부",
                "주문번호",
                "체결 여부",
                "처리 결과",
                "주문 결과",
            )
            has_submission_stall = any(term in masked_text for term in submission_stall_terms)
            has_result_question = any(term in masked_text for term in result_terms)
            if has_submission_stall and not has_result_question:
                return True

        return False

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> tuple[str | None, ExtractFailureReason | None, str | None]:
        """LLM을 호출하고 원본 응답 텍스트를 반환한다. 실패하면 (None, 사유, 상세)."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=cast("Iterable[ChatCompletionMessageParam]", messages),
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except APITimeoutError as e:
            return None, ExtractFailureReason.TIMEOUT, str(e)
        except Exception as e:
            return None, ExtractFailureReason.PROVIDER_UNAVAILABLE, str(e)

        choice = response.choices[0]
        content = choice.message.content

        if content is None or not content.strip():
            finish_reason = getattr(choice, "finish_reason", "unknown")
            return (
                None,
                ExtractFailureReason.PROVIDER_UNAVAILABLE,
                f"LLM 응답 content가 비어있습니다 (finish_reason={finish_reason})",
            )

        return content, None, None

    def _classify_issue_type_focused(self, masked_text: str) -> tuple[IssueType, str | None] | None:
        """
        짧은 전용 호출로 issue_type과 근거 조각을 함께 판정한다.

        evidence_quote는 이 호출의 LLM이 원문에서 직접 복사한 것이어야 하며,
        masked_text의 정확한 substring이 아니면 후보 전체를 버린다 (AI-03).
        실패/모호/근거 불일치 시 None을 반환하고, 호출부는 기존 값을 그대로
        유지한다.
        """
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "고객 제보의 주된 오류유형을 분류하고 근거를 함께 반환하세요. "
                    "다음 JSON 객체 하나만 출력하고 설명이나 코드블록을 붙이지 "
                    "마세요.\n"
                    '{"issue_type": "<7개 값 중 하나>", '
                    '"evidence_quote": "<근거 문자열 또는 null>"}\n\n'
                    "issue_type 값:\n"
                    "ORDER_SUBMISSION_FAILURE: 주문 버튼·확정·전송 단계의 멈춤, "
                    "무반응, 로딩\n"
                    "ORDER_RESULT_UNCONFIRMED: 주문 후 접수·주문번호·체결 결과를 "
                    "확인할 수 없음. 체결·주문 내역 화면이 언급돼도 방금 보낸 "
                    "특정 주문의 존재·접수 여부가 불확실하면 이 값\n"
                    "LOGIN_ACCESS_FAILURE: 로그인·비밀번호·인증 과정의 실제 실패\n"
                    "BALANCE_INQUIRY_ERROR: 잔고·예수금·보유종목·체결내역·주문내역 "
                    "조회 또는 표시 오류. 단, 방금 시도한 특정 주문의 존재·접수 "
                    "여부가 핵심이면 ORDER_RESULT_UNCONFIRMED를 우선\n"
                    "DEVICE_NETWORK_SUSPECTED: 네트워크나 특정 기기와 장애의 "
                    "연관성이 명시됨. 네트워크가 정상이라고 하면 선택하지 않음\n"
                    "UNRELATED_OR_AMBIGUOUS: 장애가 아닌 수수료·일정·방법·전망 문의. "
                    "질문 앞에 상황이나 이유를 설명하는 구절이 붙어도 핵심 요청이 "
                    "정보성 질문이면 이 값입니다. 이유 설명 구절 자체를 장애 근거로 "
                    "오인해 UNKNOWN으로 후퇴하지 마세요\n"
                    "UNKNOWN: 장애는 있으나 기능과 원인을 특정할 근거가 부족함\n\n"
                    "evidence_quote는 사용자 메시지(원문)에서 분류 근거가 되는 "
                    "부분을 어미·조사까지 한 글자도 다르지 않게 그대로 복사한 "
                    "부분 문자열이어야 합니다. 요약하거나 새 문장을 만들지 "
                    "마세요. issue_type이 UNKNOWN이면 evidence_quote는 반드시 "
                    "null입니다.\n\n"
                    "우선순위: 명시적 네트워크·기기 원인, 장애 발생 기능과 단계, "
                    "정보성 문의, 마지막으로 UNKNOWN."
                ),
            },
            {"role": "user", "content": masked_text},
        ]
        content, failure_reason, _ = self._call_llm(messages, max_tokens=1024)
        if failure_reason is not None or content is None:
            return None

        try:
            parsed = json.loads(_extract_json_text(content))
        except (ValueError, json.JSONDecodeError):
            return None

        try:
            issue_type = IssueType(parsed.get("issue_type"))
        except ValueError:
            return None

        evidence_quote = parsed.get("evidence_quote")
        if issue_type is IssueType.UNKNOWN:
            return issue_type, None
        if (
            not isinstance(evidence_quote, str)
            or not evidence_quote
            or evidence_quote not in masked_text
        ):
            return None
        return issue_type, evidence_quote

    def _apply_focused_issue_type(
        self,
        masked_text: str,
        result: ExtractionResult,
    ) -> tuple[int, bool]:
        """
        전용 분류가 유효할 때만 issue_type 필드를 교체한다.

        1차 확정값이 이미 있고 로컬 규칙과 충돌하지 않으면 전용 AI를
        호출하지 않고 그대로 둔다 (불필요한 호출 절약). 충돌이 있거나
        아직 확정되지 않았으면 전용 AI를 호출하되, 최종 결정과 evidence_quote는
        항상 이 AI 응답에서만 가져온다 — 로컬 규칙은 호출 여부만 결정한다.
        """
        issue_type = result.technical.issue_type
        already_confirmed = (
            issue_type.status is FieldStatus.CONFIRMED_FROM_TEXT and issue_type.value is not None
        )
        if already_confirmed and not self._has_issue_type_conflict(masked_text, result):
            return 0, False

        classified = self._classify_issue_type_focused(masked_text)
        if classified is None:
            return 1, False
        classified_type, evidence_quote = classified
        if classified_type is not IssueType.UNKNOWN and not self._issue_type_has_minimum_evidence(
            classified_type, masked_text
        ):
            return 1, False

        previous = (issue_type.value, issue_type.status, issue_type.evidence_quote)
        if classified_type is IssueType.UNKNOWN:
            issue_type.value = None
            issue_type.status = FieldStatus.UNKNOWN
            issue_type.evidence_quote = None
        else:
            issue_type.value = classified_type
            issue_type.status = FieldStatus.CONFIRMED_FROM_TEXT
            issue_type.evidence_quote = evidence_quote

        current = (issue_type.value, issue_type.status, issue_type.evidence_quote)
        return 1, current != previous

    def _compact_full_text_issue_evidence(
        self,
        masked_text: str,
        result: ExtractionResult,
    ) -> None:
        """원문 전체인 issue_type 근거만 정확한 부분 문자열로 축소한다.

        분류값이나 상태는 변경하지 않는다. LLM 근거가 이미 부분 문자열이면
        그대로 유지하고, 원문 전체를 반환한 경우에만 확정된 유형의 명시적
        단서부터 문장 끝까지를 사용한다. 따라서 새 근거도 항상 masked_text의
        연속된 substring이다.
        """
        field = result.technical.issue_type
        if (
            field.status is not FieldStatus.CONFIRMED_FROM_TEXT
            or field.value is None
            or field.evidence_quote != masked_text
        ):
            return

        cue_terms = {
            IssueType.ORDER_SUBMISSION_FAILURE: (
                "주문 버튼",
                "주문 확인",
                "주문 제출",
                "매수 주문",
                "매도 주문",
                "주문",
            ),
            IssueType.ORDER_RESULT_UNCONFIRMED: (
                "접수 여부",
                "접수됐는지",
                "주문번호",
                "체결 여부",
                "처리 결과",
                "주문 결과",
            ),
            IssueType.LOGIN_ACCESS_FAILURE: (
                "로그인",
                "비밀번호",
                "인증번호",
                "인증",
            ),
            IssueType.BALANCE_INQUIRY_ERROR: (
                "매매 내역",
                "거래 내역",
                "체결 내역",
                "주문 내역",
                "보유 종목",
                "잔고",
                "예수금",
            ),
            IssueType.DEVICE_NETWORK_SUSPECTED: (
                "기기에서만",
                "휴대폰에서만",
                "스마트폰에서만",
                "와이파이",
                "Wi-Fi",
                "네트워크",
                "모바일 데이터",
            ),
            IssueType.UNRELATED_OR_AMBIGUOUS: (
                "배당금",
                "지급일",
                "수수료",
                "우대율",
                "계좌를 새로 개설",
                "개설하는 절차",
                "절차를",
                "방법을",
                "일정을",
                "전망",
            ),
        }
        for cue in cue_terms.get(field.value, ()):
            start = masked_text.find(cue)
            if start > 0:
                field.evidence_quote = masked_text[start:]
                return

        # 유형 단서가 문장 첫머리에만 있으면 쉼표 뒤의 실제 장애·질문 절을
        # 사용할 수 있다. 적절한 부분 절이 없으면 검증 가능한 원문을 유지한다.
        comma_index = masked_text.find(",")
        if comma_index >= 0:
            suffix = masked_text[comma_index + 1 :].lstrip()
            if suffix and suffix in masked_text:
                field.evidence_quote = suffix

    def _validate_evidence_quotes(self, masked_text: str, result: ExtractionResult) -> None:
        """모든 non-null evidence_quote가 실제 masked_text의 substring인지 검증한다."""
        fields = [
            *result.technical.__dict__.items(),
            *result.consultation.__dict__.items(),
        ]
        for field_name, candidate in fields:
            evidence = candidate.evidence_quote
            if evidence is not None and evidence not in masked_text:
                raise ValueError(
                    f"{field_name}: evidence_quote must be an exact substring "
                    f"of masked_text (evidence_quote={evidence!r})"
                )

    def _validate_order_type_semantic(self, result: ExtractionResult) -> None:
        """
        order_type semantic validator.

        CONFIRMED_FROM_TEXT인 order_type은 반드시 그 value(LIMIT/MARKET)와
        의미가 일치하는 명시적 원문 근거가 evidence_quote 안에 있어야 한다.
        (예: value=LIMIT인데 evidence에 "시장가"만 있는 교차 오류도 잡아낸다.)
        """
        order_type = result.consultation.order_type
        if order_type.status is not FieldStatus.CONFIRMED_FROM_TEXT:
            return

        evidence = order_type.evidence_quote or ""

        if order_type.value == OrderType.LIMIT and "지정가" not in evidence:
            raise OrderTypeSemanticError(
                "order_type: LIMIT로 확정했지만 evidence_quote에 "
                f"'지정가'가 없습니다. (evidence_quote={order_type.evidence_quote!r})"
            )

        if order_type.value == OrderType.MARKET and "시장가" not in evidence:
            raise OrderTypeSemanticError(
                "order_type: MARKET으로 확정했지만 evidence_quote에 "
                f"'시장가'가 없습니다. (evidence_quote={order_type.evidence_quote!r})"
            )

    def _apply_safe_order_type_fallback(self, masked_text: str, result: ExtractionResult) -> bool:
        """
        원문에 "지정가"/"시장가"라는 단어 자체가 전혀 없는데도 모델이
        order_type을 LIMIT/MARKET으로 확정한, 명백히 근거 없는 경우에만
        order_type을 UNKNOWN/null/null로 안전하게 낮춘다 (LLM 재호출 없이).

        원문에 실제로 "지정가"나 "시장가"라는 단어가 있는 경우는 여기서
        손대지 않는다 - 그건 모델이 값을 잘못 매칭했을 가능성(예: 시장가인데
        LIMIT로 분류)이라 새로운 판단이 필요한 문제이므로, 기존 correction
        retry 경로로 넘겨 LLM이 다시 판단하게 한다. 즉 이 함수는 "값을
        추론"하는 게 아니라 "근거가 아예 없는 값을 폐기"하는 것으로 동작을
        제한한다.
        """
        order_type = result.consultation.order_type

        if order_type.status is not FieldStatus.CONFIRMED_FROM_TEXT:
            return False

        if "지정가" in masked_text or "시장가" in masked_text:
            return False

        order_type.value = None
        order_type.status = FieldStatus.UNKNOWN
        order_type.evidence_quote = None
        return True

    def _run_post_validation(self, masked_text: str, result: ExtractionResult) -> None:
        """Pydantic 검증 이후 evidence grounding과 semantic validation을 수행한다."""
        self._validate_evidence_quotes(masked_text, result)
        self._validate_order_type_semantic(result)

    def _parse_and_validate(
        self, masked_text: str, raw_content: str
    ) -> tuple[
        ExtractionResult | None,
        ExtractFailureReason | None,
        str | None,
        bool,
    ]:
        """
        LLM 응답을 파싱/검증한다.

        UNKNOWN 필드의 null 표현 정규화, 근거 없는 order_type 제거만
        deterministic fallback으로 처리한다. issue_type의 확정/재확정은
        여기서 하지 않는다 (extract_safe()의 _apply_focused_issue_type() 참고).
        새로운 주문 세부정보나 날짜 값은 생성하지 않는다.
        """
        try:
            cleaned = _extract_json_text(raw_content)
        except ValueError as e:
            return None, ExtractFailureReason.INVALID_JSON, f"{e}\n원본: {raw_content}", False

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            return None, ExtractFailureReason.INVALID_JSON, f"{e}\n원본: {raw_content}", False

        try:
            fallback_applied = self._normalize_unknown_fields(parsed)
            tech = parsed["technical"]
            tech["issue_type"] = self._coerce_types(tech["issue_type"], IssueType)
            tech["symptom"] = self._coerce_types(tech["symptom"], None)
            tech["submission_status"] = self._coerce_types(
                tech["submission_status"], SubmissionStatus
            )
            tech["error_code"] = self._coerce_types(tech["error_code"], None)
            tech["reported_occurred_at"] = self._coerce_types(tech["reported_occurred_at"], None)

            cons = parsed["consultation"]
            cons["action"] = self._coerce_types(cons["action"], OrderAction)
            cons["symbol_name"] = self._coerce_types(cons["symbol_name"], None)
            cons["symbol_code"] = self._coerce_types(cons["symbol_code"], None)
            cons["quantity"] = self._coerce_types(cons["quantity"], int)
            cons["order_type"] = self._coerce_types(cons["order_type"], OrderType)
            cons["price_krw"] = self._coerce_types(cons["price_krw"], int)
            cons["attempted_at"] = self._coerce_types(cons["attempted_at"], None)

            technical = TechnicalCandidate.model_validate(tech)
            consultation = ConsultationCandidate.model_validate(cons)

            result = ExtractionResult(
                schema_version="dual-extraction.v1",
                taxonomy_version="issue-type-canonical.v1",
                adapter_name="openai",
                model_id=self._model,
                technical=technical,
                consultation=consultation,
            )

            fallback_applied |= self._apply_safe_order_type_fallback(masked_text, result)
            # issue_type의 확정/재확정은 여기서 하지 않는다. extract_safe()가
            # _parse_and_validate() 이후 _apply_focused_issue_type()을 통해
            # 전용 AI 호출로만 처리한다 (3차 회의 12장: 오류 유형 분류 = AI 담당).
            self._run_post_validation(masked_text, result)

        except KeyError as e:
            return (
                None,
                ExtractFailureReason.INVALID_SCHEMA,
                f"필수 필드 {e}가 응답에 누락됨\n원본: {raw_content}",
                False,
            )
        except (ValueError, ValidationError) as e:
            return None, ExtractFailureReason.INVALID_SCHEMA, f"{e}\n원본: {raw_content}", False

        return result, None, None, fallback_applied

    def _build_correction_message(self, detail: str | None) -> dict[str, Any]:
        """correction 재요청용 user 메시지를 만든다."""
        # 검증 detail에는 모델의 원본 응답이 포함된다. 원본이 장황하거나
        # 교정 지시를 그대로 복사한 경우 이를 다음 요청에 전부 되먹이면
        # 프롬프트가 기하급수적으로 길어지고 다시 지시를 복사할 수 있으므로,
        # 오류 종류와 앞부분을 판별하기에 충분한 길이만 전달한다.
        correction_detail = (detail or "상세 오류 없음")[:2000]
        return {
            "role": "user",
            "content": (
                "이전 응답을 설명하지 말고 수정된 JSON 객체 하나만 반환하세요. "
                "교정 지시, 분석, 코드블록을 응답에 복사하지 마세요.\n\n"
                "이전 응답의 오류 내용:\n"
                f"{correction_detail}\n\n"
                "특히 status가 NEEDS_CONFIRMATION, UNKNOWN, OUT_OF_SCOPE인 "
                "필드는 value가 반드시 null이어야 합니다.\n\n"
                "날짜·시각 규칙을 다시 확인하세요. 원문에 시각만 있고 연도·월·일이 "
                "없으면 reported_occurred_at과 attempted_at에 임의의 날짜를 생성하지 "
                "말고 NEEDS_CONFIRMATION + value=null로 처리하세요.\n\n"
                "반대로 원문에 4자리 또는 2자리 연도, 월, 일, 시각이 모두 "
                "명시되어 있으면 완전한 날짜·시각이므로 반드시 "
                "CONFIRMED_FROM_TEXT로 처리하세요. 완전한 날짜·시각을 "
                "NEEDS_CONFIRMATION으로 낮추면 안 됩니다.\n\n"
                "원문에서 연도가 두 자리인 경우 앞에 '20'을 붙여 4자리 연도로 "
                "정규화하세요. 예를 들어 '26년'은 '2026년'으로 해석합니다. "
                "value는 정규화된 4자리 연도와 UTC offset을 포함한 ISO 8601 "
                "형식으로 작성하세요. evidence_quote는 원문의 연도 표현을 바꾸지 "
                "말고 그대로 복사하세요. 원문이 '26년'이면 evidence_quote에도 "
                "'26년'을 사용해야 합니다.\n\n"
                "필수 필드를 절대 생략하지 마세요. technical의 issue_type, symptom, "
                "submission_status, error_code, reported_occurred_at과 consultation의 "
                "action, symbol_name, symbol_code, quantity, order_type, price_krw, "
                "attempted_at을 예외 없이 전부 포함하세요. 정보가 없는 필드도 "
                '{"value": null, "status": "UNKNOWN", "evidence_quote": null} '
                '형태로 반드시 포함해야 합니다. 특히 status가 "UNKNOWN"인데 '
                'value 필드에 문자열 "UNKNOWN"을 넣는 것은 잘못된 형식입니다. '
                "status와 value는 다른 필드입니다. status=UNKNOWN이면 value는 "
                "반드시 null이어야 하며 evidence_quote도 반드시 null이어야 "
                "합니다. UNKNOWN이라는 enum 문자열을 value에 넣지 마세요.\n\n"
                "issue_type 분류 경계를 다시 확인하세요. 주문 버튼·확인·제출 "
                "단계의 멈춤이나 로딩은 ORDER_SUBMISSION_FAILURE, 주문 시도 후 "
                "접수 여부·주문번호·체결 여부를 모르면 "
                "ORDER_RESULT_UNCONFIRMED입니다. 체결·주문 내역 화면이 언급돼도 "
                "방금 보낸 특정 주문의 존재 여부가 불확실하면 이 유형을 우선하세요. "
                "잔고·보유수량·일반적인 체결내역·주문내역의 "
                "조회 오류는 BALANCE_INQUIRY_ERROR입니다. 와이파이·모바일 "
                "데이터·특정 기기와 장애의 연관성이 명시되면 "
                "DEVICE_NETWORK_SUSPECTED입니다. 장애가 아닌 수수료·일정·전망 "
                "문의는 UNRELATED_OR_AMBIGUOUS이고, 장애 표현은 있지만 기능을 "
                "특정할 수 없을 때만 issue_type을 UNKNOWN 형식(value=null, "
                "status=UNKNOWN, evidence_quote=null)으로 처리하세요.\n\n"
                "issue_type을 분류할 수 있으면 IssueType 문자열은 반드시 value에 "
                "넣고 status는 CONFIRMED_FROM_TEXT로 작성하세요. evidence_quote는 "
                "현재 고객 제보에서 정확히 복사하세요. IssueType 문자열을 status에 "
                "넣지 마세요.\n\n"
                'order_type은 원문에 정확히 "지정가" 또는 "시장가"라는 단어가 '
                '있을 때만 채우세요. "매도 주문을 넣었다"는 표현만으로는 '
                "LIMIT 또는 MARKET을 추론할 수 없습니다. 이런 경우 반드시 "
                "value=null, status=UNKNOWN, evidence_quote=null로 처리하세요. "
                "원문에 없는 단어를 근거로 생성하지 마세요.\n\n"
                "evidence_quote는 원문을 요약하거나 의역하지 말고 어미와 조사까지 "
                "한 글자도 다르지 않게 정확히 복사한 부분 문자열이어야 합니다. "
                "새로운 문장을 만들지 말고 원문에서 관련 부분을 그대로 "
                "복사하세요.\n\n"
                "attempted_at은 주식 주문 시도 시각에만 사용하세요. 로그인, 앱 실행, "
                "이체, 조회 등 주문과 무관한 행동의 시각을 attempted_at에 넣으면 "
                "안 됩니다. 로그인 관련 문의는 issue_type을 "
                "LOGIN_ACCESS_FAILURE로 분류하되 주문 관련 필드는 UNKNOWN으로 "
                "처리하세요.\n\n"
                "반드시 JSON 객체만 반환하세요. 설명, 분석, 코드블록 표시(```), "
                "JSON 앞뒤 문장을 출력하지 마세요. 응답의 첫 문자는 { 이어야 하고 "
                "마지막 문자는 } 이어야 합니다.\n\n"
                "이 규칙을 모두 지켜 동일한 제보를 처음부터 다시 분석하세요."
            ),
        }

    def extract_safe(self, masked_text: str) -> ExtractOutcome:
        """
        AI-07 계약에 맞게, 실패 유형을 구분해서 안전하게 반환한다.

        1차 응답에서 UNKNOWN의 순수 표현 오류를 정규화하고, 명시 단서가 있는
        issue_type과 근거 없는 order_type만 로컬 안전 보정한다. 그 외 실패는
        위반 내용을 알려주는 correction 메시지와 함께 최대 2회 재요청한다.

        재시도를 2회로 늘린 이유: evidence_quote 재조합(원문을 어미까지
        똑같이 복사하지 않고 요약/재구성)처럼 모델이 확률적으로 실수하는
        유형은, 1회 재시도로는 복구가 안 되는 경우가 실사용에서 관찰되었다.
        2회까지는 "같은 실수를 반복하는 모델을 계속 붙잡고 재시도"하는
        비용이 합리적이라고 판단했다. 그 이상은 무한 재시도로 이어질 위험이
        있어 2회로 제한한다.
        """
        if not masked_text:
            raise ValueError("masked_text cannot be empty")

        base_messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"다음 고객 제보를 분석해주세요:\n\n{masked_text}"},
        ]

        # --- 1차 시도 ---
        raw_content, failure_reason, detail = self._call_llm(base_messages)
        if failure_reason is not None:
            return ExtractOutcome(None, failure_reason, detail, attempt_count=1)
        assert raw_content is not None  # failure_reason이 None이면 항상 값이 있다

        result, failure_reason, detail, fallback_applied = self._parse_and_validate(
            masked_text, raw_content
        )
        if result is not None:
            classifier_calls, classifier_override = self._apply_focused_issue_type(
                masked_text,
                result,
            )
            self._compact_full_text_issue_evidence(masked_text, result)
            return ExtractOutcome(
                result,
                None,
                None,
                attempt_count=1,
                first_pass_valid=True,
                semantic_fallback_applied=fallback_applied,
                classification_call_count=classifier_calls,
                classification_override_applied=classifier_override,
            )

        first_failure_reason = failure_reason
        first_failure_detail = detail

        # --- 실패 시: 최대 2회까지 correction 재요청 ---
        last_failure_reason = failure_reason
        last_detail = detail
        last_raw_content = raw_content

        max_retries = 2
        for attempt in range(2, 2 + max_retries):
            correction_messages = base_messages + [
                {"role": "assistant", "content": last_raw_content},
                self._build_correction_message(last_detail),
            ]

            raw_content_n, failure_reason_n, detail_n = self._call_llm(correction_messages)
            if failure_reason_n is not None:
                return ExtractOutcome(
                    None,
                    failure_reason_n,
                    detail_n,
                    attempt_count=attempt,
                    first_failure_reason=first_failure_reason,
                    first_failure_detail=first_failure_detail,
                )
            assert raw_content_n is not None

            result_n, failure_reason_n, detail_n, fallback_applied_n = self._parse_and_validate(
                masked_text, raw_content_n
            )
            if result_n is not None:
                classifier_calls, classifier_override = self._apply_focused_issue_type(
                    masked_text, result_n
                )
                self._compact_full_text_issue_evidence(masked_text, result_n)
                return ExtractOutcome(
                    result_n,
                    None,
                    None,
                    attempt_count=attempt,
                    first_pass_valid=False,
                    semantic_fallback_applied=fallback_applied_n,
                    first_failure_reason=first_failure_reason,
                    first_failure_detail=first_failure_detail,
                    classification_call_count=classifier_calls,
                    classification_override_applied=classifier_override,
                )

            last_failure_reason = failure_reason_n
            last_detail = detail_n
            last_raw_content = raw_content_n

        return ExtractOutcome(
            None,
            last_failure_reason,
            last_detail,
            attempt_count=2 + max_retries - 1,
            first_pass_valid=False,
            first_failure_reason=first_failure_reason,
            first_failure_detail=first_failure_detail,
        )


if __name__ == "__main__":
    extractor = RealDualExtractor()

    test_text = "2026년 8월 15일 오전 9시 3분에 삼성전자 매도 주문을 넣었는데 계속 로딩만 됩니다."
    print("입력:", test_text)
    print("모델:", extractor._model)
    print("요청 시작...")
    start = time.time()

    outcome = extractor.extract_safe(test_text)

    elapsed = time.time() - start
    print(f"완료까지 걸린 시간: {elapsed:.1f}초")
    print()

    if outcome.result is not None:
        print(
            f"성공 (attempt_count={outcome.attempt_count}, "
            f"first_pass_valid={outcome.first_pass_valid}):"
        )
        print(outcome.result.model_dump_json(indent=2, ensure_ascii=False))
    else:
        print(f"실패 ({outcome.failure_reason}, attempt_count={outcome.attempt_count}):")
        print(outcome.detail)
