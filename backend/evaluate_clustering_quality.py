from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import combinations
from random import Random

from app.clustering import (
    CLUSTERING_POLICY_VERSION,
    SIMILARITY_THRESHOLD,
    group_similar_reports,
)
from app.embedding import get_symptom_embedding
from evaluate_embedding_pairs import CASES


EXCLUDED_ISSUE_TYPES = {"UNKNOWN", "UNRELATED_OR_AMBIGUOUS"}
ORDER_SEEDS = range(10)
THRESHOLD_CANDIDATES = [value / 100 for value in range(79, 86)]


def canonical_member_sets(groups):
    """대표와 그룹 출력 순서를 제외한 군집 멤버 집합."""
    return frozenset(
        frozenset(member.technical_symptom_id for member in group)
        for group in groups
    )


def membership_by_id(groups):
    memberships = {}
    for group_index, group in enumerate(groups):
        for member in group:
            memberships[member.technical_symptom_id] = group_index
    return memberships


def safe_divide(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def build_similarity_cache(reports):
    embedding_by_id = {
        report_id: embedding
        for report_id, _, embedding in reports
    }
    cache = {}
    for left_id, right_id in combinations(sorted(embedding_by_id), 2):
        left = embedding_by_id[left_id]
        right = embedding_by_id[right_id]
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        denominator = left_norm * right_norm
        cache[(left_id, right_id)] = (
            sum(a * b for a, b in zip(left, right, strict=True)) / denominator
            if denominator
            else 0.0
        )
    return cache


def cached_similarity(cache, left_id, right_id):
    if left_id == right_id:
        return 1.0
    return cache[tuple(sorted((left_id, right_id)))]


def partition_report_ids(reports):
    candidates_by_issue_type = defaultdict(list)
    excluded_ids = []
    for report_id, issue_type, _ in reports:
        if issue_type in EXCLUDED_ISSUE_TYPES:
            excluded_ids.append(report_id)
        else:
            candidates_by_issue_type[issue_type].append(report_id)
    return candidates_by_issue_type, excluded_ids


def cluster_medoid_candidate(reports, cache, threshold):
    candidates_by_issue_type, excluded_ids = partition_report_ids(reports)
    result = []
    for issue_type in sorted(candidates_by_issue_type):
        remaining = set(candidates_by_issue_type[issue_type])
        while remaining:
            representative_id = min(
                remaining,
                key=lambda report_id: (
                    -sum(
                        cached_similarity(cache, report_id, other_id)
                        for other_id in remaining
                        if other_id != report_id
                    ) / max(len(remaining) - 1, 1),
                    report_id,
                ),
            )
            members = frozenset(
                report_id
                for report_id in remaining
                if cached_similarity(cache, representative_id, report_id)
                >= threshold
            )
            result.append(members)
            remaining.difference_update(members)
    result.extend(frozenset({report_id}) for report_id in sorted(excluded_ids))
    return frozenset(result)


def cluster_agglomerative_candidate(reports, cache, threshold, linkage):
    candidates_by_issue_type, excluded_ids = partition_report_ids(reports)
    result = []
    for issue_type in sorted(candidates_by_issue_type):
        clusters = [
            frozenset({report_id})
            for report_id in sorted(candidates_by_issue_type[issue_type])
        ]
        while True:
            merge_candidates = []
            for left_index in range(len(clusters)):
                for right_index in range(left_index + 1, len(clusters)):
                    left_cluster = clusters[left_index]
                    right_cluster = clusters[right_index]
                    cross_scores = [
                        cached_similarity(cache, left_id, right_id)
                        for left_id in left_cluster
                        for right_id in right_cluster
                    ]
                    score = (
                        min(cross_scores)
                        if linkage == "complete"
                        else sum(cross_scores) / len(cross_scores)
                    )
                    if score >= threshold:
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
        result.extend(clusters)
    result.extend(frozenset({report_id}) for report_id in sorted(excluded_ids))
    return frozenset(result)


def calculate_cluster_metrics(member_sets, case_by_id):
    membership = {
        report_id: group_index
        for group_index, member_ids in enumerate(member_sets)
        for report_id in member_ids
    }
    eligible_ids = [
        case_id
        for case_id, metadata in case_by_id.items()
        if metadata["issue_type"] not in EXCLUDED_ISSUE_TYPES
        and metadata["cluster_label"] is not None
    ]
    tp = fp = fn = tn = 0
    for left_id, right_id in combinations(eligible_ids, 2):
        true_same = (
            case_by_id[left_id]["cluster_label"]
            == case_by_id[right_id]["cluster_label"]
        )
        predicted_same = membership[left_id] == membership[right_id]
        if true_same and predicted_same:
            tp += 1
        elif not true_same and predicted_same:
            fp += 1
        elif true_same and not predicted_same:
            fn += 1
        else:
            tn += 1
    precision = safe_divide(tp, tp + fp)
    recall = safe_divide(tp, tp + fn)
    f1 = safe_divide(2 * precision * recall, precision + recall)
    return tp, fp, fn, tn, precision, recall, f1


def main():
    print(f"군집 정책 버전: {CLUSTERING_POLICY_VERSION}")
    print(f"적용 threshold: {SIMILARITY_THRESHOLD:.2f}")
    print(f"전체 평가 문장 수: {len(CASES)}\n")

    reports = []
    case_by_id = {}
    for index, (case_id, issue_type, cluster_label, symptom) in enumerate(CASES, 1):
        issue_type_value = issue_type.value
        print(
            f"[{index:02d}/{len(CASES)}] embedding: {case_id} / "
            f"{issue_type_value} / {cluster_label or '평가 제외'}"
        )
        embedding = get_symptom_embedding(symptom)
        reports.append((case_id, issue_type_value, embedding))
        case_by_id[case_id] = {
            "issue_type": issue_type_value,
            "cluster_label": cluster_label,
            "symptom": symptom,
        }

    similarity_cache = build_similarity_cache(reports)
    comparison_rows = []
    for algorithm in ("medoid", "complete", "average"):
        for threshold in THRESHOLD_CANDIDATES:
            if algorithm == "medoid":
                member_sets = cluster_medoid_candidate(
                    reports, similarity_cache, threshold
                )
            else:
                member_sets = cluster_agglomerative_candidate(
                    reports,
                    similarity_cache,
                    threshold,
                    linkage=algorithm,
                )
            metrics = calculate_cluster_metrics(member_sets, case_by_id)
            comparison_rows.append((algorithm, threshold, *metrics, member_sets))

    print("\n=== 후보 알고리즘/threshold 비교 ===")
    print("algorithm threshold  TP  FP  FN  TN  precision  recall     F1")
    for row in comparison_rows:
        algorithm, threshold, tp, fp, fn, tn, precision, recall, f1, _ = row
        print(
            f"{algorithm:>9} {threshold:>9.2f} "
            f"{tp:>3} {fp:>3} {fn:>3} {tn:>3} "
            f"{precision:>10.6f} {recall:>7.6f} {f1:>8.6f}"
        )

    precision_safe = [row for row in comparison_rows if row[6] >= 0.80]
    best_candidate = (
        max(
            precision_safe,
            key=lambda row: (row[8], row[7], row[1]),
        )
        if precision_safe
        else None
    )
    print("\n=== 자동 선정 후보 ===")
    if best_candidate is None:
        print("Precision 0.80 이상인 후보가 없습니다.")
    else:
        algorithm, threshold, tp, fp, fn, tn, precision, recall, f1, _ = (
            best_candidate
        )
        print(
            f"algorithm={algorithm}, threshold={threshold:.2f}, "
            f"TP={tp}, FP={fp}, FN={fn}, TN={tn}, "
            f"Precision={precision:.6f}, Recall={recall:.6f}, F1={f1:.6f}"
        )

    now = datetime.now(UTC)
    groups = group_similar_reports(reports, now=now)
    memberships = membership_by_id(groups)

    eligible_ids = [
        case_id
        for case_id, metadata in case_by_id.items()
        if metadata["issue_type"] not in EXCLUDED_ISSUE_TYPES
        and metadata["cluster_label"] is not None
    ]

    tp = fp = fn = tn = 0
    for left_id, right_id in combinations(eligible_ids, 2):
        left = case_by_id[left_id]
        right = case_by_id[right_id]
        true_same = left["cluster_label"] == right["cluster_label"]
        predicted_same = memberships[left_id] == memberships[right_id]

        if true_same and predicted_same:
            tp += 1
        elif not true_same and predicted_same:
            fp += 1
        elif true_same and not predicted_same:
            fn += 1
        else:
            tn += 1

    precision = safe_divide(tp, tp + fp)
    recall = safe_divide(tp, tp + fn)
    f1 = safe_divide(2 * precision * recall, precision + recall)

    print("\n=== 실제 생성 군집 ===")
    for group_index, group in enumerate(groups, 1):
        member_ids = [member.technical_symptom_id for member in group]
        labels = Counter(
            case_by_id[member_id]["cluster_label"] or "평가 제외"
            for member_id in member_ids
        )
        print(
            f"[{group_index:02d}] 대표={group[0].technical_symptom_id} "
            f"멤버={member_ids} 의미군집={dict(labels)}"
        )

    print("\n=== 최종 군집 pairwise 평가 ===")
    print(f"TP={tp}")
    print(f"FP={fp}")
    print(f"FN={fn}")
    print(f"TN={tn}")
    print(f"Precision={precision:.6f}")
    print(f"Recall={recall:.6f}")
    print(f"F1={f1:.6f}")

    predicted_groups_by_label = defaultdict(set)
    labels_by_predicted_group = defaultdict(set)
    for case_id in eligible_ids:
        label = case_by_id[case_id]["cluster_label"]
        group_index = memberships[case_id]
        predicted_groups_by_label[label].add(group_index)
        labels_by_predicted_group[group_index].add(label)

    print("\n=== 정답 의미 군집 분절 수 ===")
    for label in sorted(predicted_groups_by_label):
        group_count = len(predicted_groups_by_label[label])
        print(f"{label}: {group_count}개 예측 군집")

    print("\n=== 서로 다른 의미 군집 과병합 ===")
    overmerged = False
    for group_index, labels in sorted(labels_by_predicted_group.items()):
        if len(labels) <= 1:
            continue
        overmerged = True
        member_ids = [
            case_id
            for case_id in eligible_ids
            if memberships[case_id] == group_index
        ]
        print(
            f"예측 군집 {group_index + 1}: labels={sorted(labels)}, "
            f"members={member_ids}"
        )
    if not overmerged:
        print("없음")

    eligible_singletons = sum(
        1
        for group in groups
        if len(group) == 1
        and group[0].technical_symptom_id in eligible_ids
    )
    print(f"\n평가 대상 singleton 수: {eligible_singletons}")

    baseline = canonical_member_sets(groups)
    unstable_seeds = []
    for seed in ORDER_SEEDS:
        shuffled = list(reports)
        Random(seed).shuffle(shuffled)
        shuffled_groups = group_similar_reports(shuffled, now=now)
        if canonical_member_sets(shuffled_groups) != baseline:
            unstable_seeds.append(seed)

    reversed_groups = group_similar_reports(list(reversed(reports)), now=now)
    reversed_stable = canonical_member_sets(reversed_groups) == baseline

    print("\n=== 입력 순서 안정성 ===")
    print(f"역순 동일={reversed_stable}")
    print(f"무작위 순서 테스트={len(tuple(ORDER_SEEDS))}회")
    print(f"결과가 달라진 seed={unstable_seeds or '없음'}")

    assert reversed_stable, "역순 입력에서 군집 멤버 구성이 달라졌습니다."
    assert not unstable_seeds, (
        "무작위 입력 순서에서 군집 멤버 구성이 달라졌습니다: "
        f"{unstable_seeds}"
    )
    print("입력 순서 안정성 검증 통과")


if __name__ == "__main__":
    main()
