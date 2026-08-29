"""
symptom 임베딩 벡터를 기반으로 유사한 제보끼리 묶는다.

AI-10 정책:
- 같은 issue_type 내부에서만 임베딩 유사도를 비교한다.
- UNKNOWN/UNRELATED_OR_AMBIGUOUS는 자동 군집에서 제외한다.
- 보강 평가 데이터 기준 threshold=0.79를 사용한다.
- average-linkage로 두 군집 사이 모든 교차 유사도의 평균이 threshold
  이상일 때만 병합한다.
- 완성된 군집 안에서 평균 유사도가 가장 높은 medoid를 대표로 선택한다.
"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

CLUSTERING_POLICY_VERSION = "v4"
SIMILARITY_THRESHOLD = 0.79
EXCLUDED_ISSUE_TYPES = {
    "UNKNOWN",
    "UNRELATED_OR_AMBIGUOUS",
}


@dataclass
class ClusterMember:
    """signal_members 테이블에 대응하는 멤버십 정보."""

    technical_symptom_id: str
    cluster_representative_id: str
    similarity: float
    joined_at: datetime


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def group_similar_reports(
    reports: list[tuple[str, str, list[float]]],
    now: datetime,
) -> list[list[ClusterMember]]:
    """
    (technical_symptom_id, issue_type, embedding) 리스트를 받아서,
    issue_type이 같은 후보를 singleton 군집으로 시작해, 두 군집 사이 모든
    교차 코사인 유사도의 평균(average-link)이 SIMILARITY_THRESHOLD 이상인
    최적의 군집 쌍을 반복해서 병합한다.

    UNKNOWN/UNRELATED_OR_AMBIGUOUS 항목은 다른 항목과 자동으로 합치지 않고
    각각 단독 그룹으로 유지한다.

    병합 점수가 같으면 군집의 정렬된 ID를 기준으로 결정하고, 완성된 군집의
    대표는 군집 내 평균 유사도가 가장 높은 medoid로 선택한다. medoid 점수가
    같으면 technical_symptom_id가 작은 항목을 선택하므로 입력 순서와 무관하게
    결정론적으로 동작한다. 나머지 멤버는 medoid와의 similarity를 함께
    기록한다 (signal_members 저장용).
    """
    groups: list[list[ClusterMember]] = []
    candidates_by_issue_type: dict[str, list[tuple[str, list[float]]]] = {}
    excluded: list[tuple[str, list[float]]] = []

    for report_id, issue_type, embedding in reports:
        if issue_type in EXCLUDED_ISSUE_TYPES:
            excluded.append((report_id, embedding))
            continue
        candidates_by_issue_type.setdefault(issue_type, []).append(
            (report_id, embedding)
        )

    for issue_type in sorted(candidates_by_issue_type):
        embedding_by_id = {
            report_id: embedding
            for report_id, embedding in candidates_by_issue_type[issue_type]
        }
        clusters = [
            frozenset({report_id})
            for report_id in sorted(embedding_by_id)
        ]

        while True:
            merge_candidates = []
            for left_index in range(len(clusters)):
                for right_index in range(left_index + 1, len(clusters)):
                    left_cluster = clusters[left_index]
                    right_cluster = clusters[right_index]
                    cross_similarities = [
                        cosine_similarity(
                            embedding_by_id[left_id],
                            embedding_by_id[right_id],
                        )
                        for left_id in left_cluster
                        for right_id in right_cluster
                    ]
                    score = sum(cross_similarities) / len(cross_similarities)
                    if score >= SIMILARITY_THRESHOLD:
                        merge_candidates.append(
                            (
                                score,
                                tuple(sorted(left_cluster)),
                                tuple(sorted(right_cluster)),
                                left_index,
                                right_index,
                            )
                        )

            if not merge_candidates:
                break

            _, _, _, left_index, right_index = min(
                merge_candidates,
                key=lambda item: (-item[0], item[1], item[2]),
            )
            merged = clusters[left_index] | clusters[right_index]
            clusters = [
                cluster
                for index, cluster in enumerate(clusters)
                if index not in {left_index, right_index}
            ]
            clusters.append(merged)
            clusters.sort(key=lambda cluster: tuple(sorted(cluster)))

        for cluster in clusters:
            average_similarity_by_id = {}
            for report_id in cluster:
                other_ids = [
                    other_id
                    for other_id in cluster
                    if other_id != report_id
                ]
                average_similarity_by_id[report_id] = (
                    sum(
                        cosine_similarity(
                            embedding_by_id[report_id],
                            embedding_by_id[other_id],
                        )
                        for other_id in other_ids
                    ) / len(other_ids)
                    if other_ids
                    else 1.0
                )
            representative_id = min(
                cluster,
                key=lambda report_id: (
                    -average_similarity_by_id[report_id],
                    report_id,
                ),
            )
            group = [
                ClusterMember(
                    technical_symptom_id=representative_id,
                    cluster_representative_id=representative_id,
                    similarity=1.0,
                    joined_at=now,
                )
            ]
            group.extend(
                ClusterMember(
                    technical_symptom_id=report_id,
                    cluster_representative_id=representative_id,
                    similarity=cosine_similarity(
                        embedding_by_id[representative_id],
                        embedding_by_id[report_id],
                    ),
                    joined_at=now,
                )
                for report_id in sorted(cluster)
                if report_id != representative_id
            )
            groups.append(group)

    for report_id, _ in sorted(excluded):
        groups.append(
            [
                ClusterMember(
                    technical_symptom_id=report_id,
                    cluster_representative_id=report_id,
                    similarity=1.0,
                    joined_at=now,
                )
            ]
        )

    return groups
