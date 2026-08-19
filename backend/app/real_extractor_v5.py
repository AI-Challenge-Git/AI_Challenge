"""
실제 LLM(NVIDIA Build)을 사용한 이중 구조화 구현.
FakeDualExtractor를 대체하되, 동일한 DualExtractor 프로토콜을 따른다.

버전: v5-final (2026-08-16)
- 스키마 검증(AI-05: NEEDS_CONFIRMATION은 value=null, evidence는 선택)을
  코드에서 우회하지 않고, 위반 시 안전하게 FAILED로 처리 (AI-07)
- FE-07: 날짜 없는 시각을 LLM이 임의의 날짜와 결합해 CONFIRMED로 만드는 경우 차단
- order_type: 원문에 지정가/시장가가 전혀 없는데 모델이 LIMIT/MARKET을
  확정한 "순수 hallucination" 케이스만 deterministic fallback으로
  UNKNOWN으로 낮춘다 (LLM 재호출 없이). 원문에 실제 단서가 있는데
  잘못 분류했거나, evidence 자체를 조작한 경우는 fallback 대상이 아니며
  기존 correction retry로 넘어간다.
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
7. symptom은 고객이 겪은 관찰 가능한 현상(예: "로딩이 멈춤", "비밀번호 오류가 뜸")만
   요약하세요. 원문 문장 전체를 그대로 복사하거나, 원인을 추측해서 서술하지 마세요.
   evidence_quote도 원문 전체가 아니라 증상과 직접 관련된 부분만 인용하세요.

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

## issue_type 분류 기준 - LOGIN_ACCESS_FAILURE
다음과 같이 로그인·인증 관련 명시적 표현이 있으면 LOGIN_ACCESS_FAILURE로
분류하세요. 정보 부족을 이유로 UNKNOWN으로 후퇴하지 마세요.
- "로그인이 안 됨", "로그인하려는데", "비밀번호가 틀렸다고 나옴",
  "인증번호가 안 옴", "접속이 안 됨" 등
예: "로그인하려는데 계속 비밀번호가 틀렸다고 나와요." → issue_type = LOGIN_ACCESS_FAILURE
이 경우 symptom에는 "로그인 시도 시 비밀번호 오류 발생"처럼 관찰된 증상만
간결하게 요약하고, submission_status/action/order_type/attempted_at 등
주문 관련 필드는 로그인 문의와 무관하므로 전부 UNKNOWN으로 두세요.

## 다시 한번 강조합니다
status가 "CONFIRMED_FROM_TEXT"일 때만 value가 non-null일 수 있습니다.
그 외 모든 status(NEEDS_CONFIRMATION, UNKNOWN, OUT_OF_SCOPE)에서는
value가 반드시 null이어야 합니다. 이 규칙을 어기면 응답 전체가 거부됩니다.

## 시각(occurred_at, attempted_at) 처리 규칙
- 날짜와 시각이 모두 명확할 때만 "CONFIRMED_FROM_TEXT"를 사용하고,
  value는 ISO 8601 형식(예: "2026-08-15T09:03:00+09:00")으로 작성하세요.
- "9시 3분쯤"처럼 날짜 없이 시각만 있으면 반드시 "NEEDS_CONFIRMATION"으로 표시하고
  value는 null로 두세요.

예시 - 연·월·일과 시각이 모두 있는 경우:
원문: "2026년 8월 15일 오전 9시 3분에 매도 주문을 넣었다"
reported_occurred_at:
{{"value": "2026-08-15T09:03:00+09:00", "status": "CONFIRMED_FROM_TEXT",
   "evidence_quote": "2026년 8월 15일 오전 9시 3분에"}}
attempted_at:
{{"value": "2026-08-15T09:03:00+09:00", "status": "CONFIRMED_FROM_TEXT",
   "evidence_quote": "2026년 8월 15일 오전 9시 3분에"}}
(reported_occurred_at과 attempted_at을 항상 똑같이 채워야 한다는 뜻은 아닙니다.
문맥상 같은 시점을 가리킬 때만 같은 값을 사용하세요.)

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

    attempt_count: 실제 LLM 호출 횟수 (1 또는 2)
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
    """

    result: ExtractionResult | None
    failure_reason: ExtractFailureReason | None
    detail: str | None = None
    attempt_count: int = 1
    first_pass_valid: bool = True
    semantic_fallback_applied: bool = False
    first_failure_reason: ExtractFailureReason | None = None
    first_failure_detail: str | None = None


class RealDualExtractor:
    """NVIDIA Build API를 사용해 실제로 이중 구조화를 수행하는 extractor."""

    def __init__(self) -> None:
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError(".env에 NVIDIA_API_KEY가 설정되어 있지 않습니다.")

        self._client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=api_key,
            timeout=90.0,
            max_retries=1,
        )
        self._model = "meta/llama-3.1-8b-instruct"

    def _coerce_types(
        self, field_dict: dict[str, Any], value_type: type | None
    ) -> dict[str, Any]:
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
        if field_dict.get("status") is not None:
            field_dict["status"] = FieldStatus(field_dict["status"])

        if (
            field_dict["status"] == FieldStatus.UNKNOWN
            and field_dict.get("value") == "UNKNOWN"
        ):
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

    def _call_llm(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str | None, ExtractFailureReason | None, str | None]:
        """LLM을 호출하고 원본 응답 텍스트를 반환한다. 실패하면 (None, 사유, 상세)."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=cast(
                    "Iterable[ChatCompletionMessageParam]", messages
                ),
                temperature=0.0,
                max_tokens=1500,
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

    def _validate_evidence_quotes(
        self, masked_text: str, result: ExtractionResult
    ) -> None:
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

    def _apply_safe_order_type_fallback(
        self, masked_text: str, result: ExtractionResult
    ) -> bool:
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

    def _run_post_validation(
        self, masked_text: str, result: ExtractionResult
    ) -> None:
        """Pydantic 검증 이후 evidence grounding과 semantic validation을 수행한다."""
        self._validate_evidence_quotes(masked_text, result)
        self._validate_order_type_semantic(result)

    def _parse_and_validate(
        self, masked_text: str, raw_content: str
    ) -> tuple[ExtractionResult | None, ExtractFailureReason | None, str | None]:
        """
        LLM 응답을 파싱/검증한다.

        order_type의 근거 없는 LIMIT/MARKET 확정만 deterministic fallback으로
        UNKNOWN 처리한다. 다른 schema/evidence 오류는 자동 보정하지 않는다.
        """
        try:
            cleaned = _extract_json_text(raw_content)
        except ValueError as e:
            return None, ExtractFailureReason.INVALID_JSON, f"{e}\n원본: {raw_content}"

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            return None, ExtractFailureReason.INVALID_JSON, f"{e}\n원본: {raw_content}"

        try:
            tech = parsed["technical"]
            tech["issue_type"] = self._coerce_types(tech["issue_type"], IssueType)
            tech["symptom"] = self._coerce_types(tech["symptom"], None)
            tech["submission_status"] = self._coerce_types(
                tech["submission_status"], SubmissionStatus
            )
            tech["error_code"] = self._coerce_types(tech["error_code"], None)
            tech["reported_occurred_at"] = self._coerce_types(
                tech["reported_occurred_at"], None
            )

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
                taxonomy_version="issue-type.v1",
                adapter_name="nvidia-build",
                model_id=self._model,
                technical=technical,
                consultation=consultation,
            )

            self._apply_safe_order_type_fallback(masked_text, result)
            self._run_post_validation(masked_text, result)

        except KeyError as e:
            return (
                None,
                ExtractFailureReason.INVALID_SCHEMA,
                f"필수 필드 {e}가 응답에 누락됨\n원본: {raw_content}",
            )
        except (ValueError, ValidationError) as e:
            return None, ExtractFailureReason.INVALID_SCHEMA, f"{e}\n원본: {raw_content}"

        return result, None, None

    def extract_safe(self, masked_text: str) -> ExtractOutcome:
        """
        AI-07 계약에 맞게, 실패 유형을 구분해서 안전하게 반환한다.

        1차 응답이 스키마/evidence 검증에 실패하면, order_type 순수
        hallucination(원문에 단서 자체가 없는 경우)만 로컬에서 UNKNOWN으로
        낮추고 LLM을 다시 부르지 않는다. 그 외 실패는 위반 내용을 알려주는
        correction 메시지와 함께 딱 1회만 재요청한다.
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

        result, failure_reason, detail = self._parse_and_validate(
            masked_text, raw_content
        )
        if result is not None:
            return ExtractOutcome(
                result, None, None, attempt_count=1, first_pass_valid=True
            )

        first_failure_reason = failure_reason
        first_failure_detail = detail

        # --- 1차 실패: 위반 내용을 포함해 1회만 correction 재요청 ---
        correction_messages = base_messages + [
            {"role": "assistant", "content": raw_content},
            {
                "role": "user",
                "content": (
                    "이전 응답이 규칙을 위반했습니다. 오류 내용:\n"
                    f"{detail}\n\n"
                    "특히 status가 NEEDS_CONFIRMATION, UNKNOWN, OUT_OF_SCOPE인 "
                    "필드는 value가 반드시 null이어야 합니다. 또한 날짜 없는 시각만 "
                    "원문에 있으면 reported_occurred_at/attempted_at에 날짜를 생성하지 말고 "
                    "NEEDS_CONFIRMATION + value=null로 처리하세요.\n\n"
                    "필수 필드를 절대 생략하지 마세요. technical의 issue_type, symptom, "
                    "submission_status, error_code, reported_occurred_at과 consultation의 "
                    "action, symbol_name, symbol_code, quantity, order_type, price_krw, "
                    "attempted_at을 예외 없이 전부 포함하세요. 정보가 없는 필드도 "
                    "{\"value\": null, \"status\": \"UNKNOWN\", \"evidence_quote\": null} "
                    "형태로 반드시 포함해야 합니다. 특히 status가 \"UNKNOWN\"인데 "
                    "value 필드에 문자열 \"UNKNOWN\"을 넣는 것은 잘못된 형식입니다 "
                    "(status와 value는 다른 필드입니다). status=UNKNOWN이면 value는 "
                    "반드시 null(값 없음)이어야 합니다.\n\n"
                    "order_type은 원문에 정확히 \"지정가\" 또는 \"시장가\"라는 단어가 "
                    "있을 때만 채우세요. \"매도 주문을 넣었다\"는 표현만으로는 "
                    "LIMIT/MARKET을 추론할 근거가 없으므로 반드시 value=null, "
                    "status=UNKNOWN입니다. 원문에 없는 단어(예: \"지정가\")를 근거로 "
                    "지어내지 마세요. 모든 evidence_quote는 원문을 요약하거나 "
                    "의역하지 말고 어미까지 한 글자도 다르지 않게 정확히 그대로 "
                    "복사(copy-paste)해야 합니다. "
                    "attempted_at은 주문 시도 시각에만 사용하고, 로그인 등 "
                    "다른 행동의 시각을 넣지 마세요. 로그인 관련 문의는 "
                    "issue_type=LOGIN_ACCESS_FAILURE로 분류하되 주문 관련 필드는 "
                    "전부 UNKNOWN으로 두세요.\n\n"
                    "반드시 JSON 객체만 반환하세요. 설명, 분석, 코드블록 표시(```), "
                    "JSON 앞뒤 문장을 절대 출력하지 마세요. 응답의 첫 문자는 { 이어야 "
                    "하고 마지막 문자는 } 이어야 합니다.\n\n"
                    "이 규칙을 모두 지켜서 동일한 제보에 대해 처음부터 다시 응답하세요."
                ),
            },
        ]

        raw_content_2, failure_reason_2, detail_2 = self._call_llm(correction_messages)
        if failure_reason_2 is not None:
            return ExtractOutcome(
                None,
                failure_reason_2,
                detail_2,
                attempt_count=2,
                first_failure_reason=first_failure_reason,
                first_failure_detail=first_failure_detail,
            )
        assert raw_content_2 is not None  # failure_reason_2가 None이면 항상 값이 있다

        result_2, failure_reason_2, detail_2 = self._parse_and_validate(
            masked_text, raw_content_2
        )
        if result_2 is not None:
            return ExtractOutcome(
                result_2,
                None,
                None,
                attempt_count=2,
                first_pass_valid=False,
                first_failure_reason=first_failure_reason,
                first_failure_detail=first_failure_detail,
            )

        return ExtractOutcome(
            None,
            failure_reason_2,
            detail_2,
            attempt_count=2,
            first_pass_valid=False,
            first_failure_reason=first_failure_reason,
            first_failure_detail=first_failure_detail,
        )


if __name__ == "__main__":
    extractor = RealDualExtractor()

    test_text = (
        "2026년 8월 15일 오전 9시 3분에 삼성전자 매도 주문을 넣었는데 "
        "계속 로딩만 됩니다."
    )
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
