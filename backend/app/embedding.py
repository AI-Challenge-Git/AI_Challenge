"""
증상(symptom) 텍스트를 임베딩 벡터로 변환한다.

AI-09 계약 (팀 확정):
- model: nvidia/nemotron-3-embed-1b
- dimension: 2048
- normalization: L2 정규화 적용
- distance_metric: cosine

모델 근거: 34개 언어(한국어 포함) 평가 완료, 2026-07-16 출시,
RTEB 리더보드 기준 이전 세대 embedding 모델보다 성능 우수.
참고: https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-embed-1b
"""

import os

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
)

EMBEDDING_MODEL = "nvidia/nemotron-3-embed-1b"
EMBEDDING_DIMENSION = 2048
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
    """symptom 텍스트를 2048차원 L2 정규화된 임베딩 벡터로 변환한다."""
    response = _client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[symptom_text],
        extra_body={"input_type": "passage"},
    )
    raw_vector = response.data[0].embedding
    return _l2_normalize(raw_vector)