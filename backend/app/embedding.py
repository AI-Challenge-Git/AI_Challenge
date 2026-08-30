"""
증상(symptom) 텍스트를 임베딩 벡터로 변환한다.

AI-09 계약 (2026-08-30 재확정):
- provider: OpenAI
- model: text-embedding-3-small
- dimension: 1024 (공식 dimensions 파라미터 사용)
- normalization: L2 정규화 적용
- distance_metric: cosine

API 요청에서 dimensions=1024를 지정하며 클라이언트에서 벡터를 임의로 자르지
않는다. 모델 변경으로 이전 NVIDIA 모델의 threshold는 승계하지 않고, 고정
평가셋으로 threshold와 군집 품질을 다시 평가한다.

참고: https://developers.openai.com/api/docs/models/text-embedding-3-small
"""

import os

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1024
NORMALIZATION = "l2"
DISTANCE_METRIC = "cosine"


def _l2_normalize(vector: list[float]) -> list[float]:
    """L2 정규화: 벡터를 단위 벡터(길이 1)로 변환한다."""
    arr = np.array(vector)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vector
    result: list[float] = (arr / norm).tolist()
    return result


def get_symptom_embedding(symptom_text: str) -> list[float]:
    """symptom 텍스트를 1024차원 L2 정규화된 임베딩 벡터로 변환한다."""
    response = _client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[symptom_text],
        dimensions=EMBEDDING_DIMENSION,
        encoding_format="float",
    )
    raw_vector = response.data[0].embedding
    if len(raw_vector) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"임베딩 차원 불일치: expected={EMBEDDING_DIMENSION}, actual={len(raw_vector)}"
        )
    return _l2_normalize(raw_vector)
