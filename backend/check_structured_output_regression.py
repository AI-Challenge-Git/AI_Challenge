"""
d160_res_05가 use_structured_output=True에서 correction retry 3회 전부
<NO_RESULT>로 실패하는 원인 진단.

extract_safe()와 같은 순서(1차 -> correction 2회)로 직접 호출하되, 매
시도의 raw_content/실패 detail을 전부 출력한다. extract_safe()는
first_failure_detail(1차)과 detail(마지막)만 노출해서 중간(2차) 시도가
안 보이므로, 내부 메서드를 그대로 재사용해 3번 다 들여다본다.

실제 OpenAI API를 호출하므로 비용이 발생한다 (직접 실행해서 확인).
"""

from app.real_extractor_v5 import _EXTRACTION_RESULT_JSON_SCHEMA, _SYSTEM_PROMPT, RealDualExtractor

TEXT = "현대차 매도 주문 후 체결 내역에 아무것도 안 보여서 접수 여부를 모르겠어요."

extractor = RealDualExtractor(use_structured_output=True)

base_messages = [
    {"role": "system", "content": _SYSTEM_PROMPT},
    {"role": "user", "content": f"다음 고객 제보를 분석해주세요:\n\n{TEXT}"},
]

raw_content, failure_reason, detail = extractor._call_llm(
    base_messages, response_format_override=_EXTRACTION_RESULT_JSON_SCHEMA
)
print("=== 1차 시도 ===")
print("failure_reason:", failure_reason)
print("detail:", detail)
print("raw_content:")
print(raw_content)

if failure_reason is None and raw_content is not None:
    result, failure_reason2, detail2, fallback = extractor._parse_and_validate(TEXT, raw_content)
    print("\n_parse_and_validate 결과:")
    print("result is None:", result is None)
    print("failure_reason:", failure_reason2)
    print("detail:", detail2)

    last_raw_content = raw_content
    last_detail = detail2
    for attempt in (2, 3):
        correction_messages = base_messages + [
            {"role": "assistant", "content": last_raw_content},
            extractor._build_correction_message(last_detail),
        ]
        raw_content_n, failure_reason_n, detail_n = extractor._call_llm(
            correction_messages, response_format_override=_EXTRACTION_RESULT_JSON_SCHEMA
        )
        print(f"\n=== {attempt}차 시도 ===")
        print("failure_reason:", failure_reason_n)
        print("detail:", detail_n)
        print("raw_content:")
        print(raw_content_n)
        if failure_reason_n is not None or raw_content_n is None:
            break
        result_n, failure_reason_n2, detail_n2, fallback_n = extractor._parse_and_validate(
            TEXT, raw_content_n
        )
        print(f"\n{attempt}차 _parse_and_validate 결과:")
        print("result is None:", result_n is None)
        print("failure_reason:", failure_reason_n2)
        print("detail:", detail_n2)
        last_raw_content = raw_content_n
        last_detail = detail_n2
