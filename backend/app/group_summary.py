"""
군집화된 그룹의 대표 오류유형과 공통 증상을 요약한다.

오류유형은 최빈값으로 집계하고, 공통 증상은 임베딩 medoid 방식으로
그룹을 가장 잘 대표하는 실제 증상 문장을 선택한다.
"""

from collections import Counter

from app.clustering import cosine_similarity


def summarize_group_issue_type(issue_types: list[str]) -> str:
    """
    그룹 내 issue_type을 요약한다.

    전부 같은 값이면 그 값을 그대로 반환하고, 섞여 있으면
    가장 많이 나온 값과 함께 "혼재" 여부를 알 수 있도록
    최빈값을 반환하되 향후 UI에서 분포를 같이 보여줄 수 있도록
    Counter 자체도 별도 함수로 노출한다.
    """
    if not issue_types:
        return "UNKNOWN"
    return Counter(issue_types).most_common(1)[0][0]


def get_issue_type_distribution(issue_types: list[str]) -> dict[str, int]:
    """그룹 내 issue_type별 분포를 반환한다 (운영 상황판 표시용)."""
    return dict(Counter(issue_types))


def summarize_group_symptoms(
    symptoms: list[tuple[str, list[float]]],
) -> str:
    """
    그룹 내 공통 증상을 요약한다.

    기존 임베딩을 재사용하여, 그룹 내 다른 증상들과의 평균 cosine
    similarity가 가장 높은 실제 문장을 대표 증상으로 선택한다.
    평균 유사도가 같으면 문장 자체를 기준으로 선택하여 입력 순서에
    영향을 받지 않도록 한다.
    """
    if not symptoms:
        return "증상 정보 없음"

    if len(symptoms) == 1:
        return symptoms[0][0]

    scored_symptoms: list[tuple[float, str]] = []

    for index, (symptom, embedding) in enumerate(symptoms):
        similarities = [
            cosine_similarity(embedding, other_embedding)
            for other_index, (_, other_embedding) in enumerate(symptoms)
            if other_index != index
        ]
        average_similarity = sum(similarities) / len(similarities)
        scored_symptoms.append((average_similarity, symptom))

    return max(
        scored_symptoms,
        key=lambda item: (item[0], item[1]),
    )[1]
