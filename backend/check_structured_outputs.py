"""
Structured Outputs(.parse())가 지금 쓰는 제네릭 CandidateField[T] 구조의
ExtractionResult와 실제로 호환되는지 확인하는 실험 스크립트.

호환 안 되면(예: strict schema가 Generic을 못 만들거나, $ref 관련 에러) 실제
_call_llm() 전환 작업 전에 스키마 자체를 조정해야 한다는 뜻이다.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

from app.schemas import ExtractionResult

load_dotenv()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

try:
    schema = ExtractionResult.model_json_schema()
    print("model_json_schema() 생성 성공, top-level keys:", list(schema.keys()))
except Exception as e:
    print("model_json_schema() 자체가 실패:", type(e).__name__, e)
    raise SystemExit(1)

print("\n.parse() 실제 호출 시도 중...")
try:
    completion = client.chat.completions.parse(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "테스트용 더미 응답을 만드세요. 모든 필드는 UNKNOWN 상태로, "
                    "value와 evidence_quote는 전부 null로 채우세요. schema_version은 "
                    "'v1', taxonomy_version은 'v1', adapter_name은 'test', model_id는 null."
                ),
            },
            {"role": "user", "content": "테스트"},
        ],
        response_format=ExtractionResult,
    )
    parsed = completion.choices[0].message.parsed
    refusal = completion.choices[0].message.refusal
    print("호출 성공")
    print("refusal:", refusal)
    print("parsed 타입:", type(parsed))
    if parsed is not None:
        print(parsed.model_dump_json(indent=2)[:500])
except Exception as e:
    print("\n.parse() 호출 실패:", type(e).__name__)
    print(e)
