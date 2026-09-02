"""
온라인 incremental 매칭 알고리즘(services/signals.py) 전용 평가.

evaluate_clustering_quality.py는 배치(batch) average-linkage(app.clustering.
group_similar_reports)를 평가한다 — 전체 제보를 한 번에 보고 최적으로 병합하며,
입력 순서와 무관하게 결과가 같아야 정상이다 (order-invariant).

반면 services/signals.py의 온라인 워커는 제보가 도착하는 순서대로 하나씩 처리하며,
매번 "새 제보 1건 vs 기존 클러스터 전체 평균 유사도"만 보고 가장 높은 클러스터에
편입하거나 새 클러스터를 만든다 (linkage_method=AVERAGE 기준). 이 방식은 도착 순서에
따라 결과가 달라질 수 있으므로, 배치 평가 결과(Precision 0.972/Recall 0.700/F1 0.814)를
온라인 워커 성능으로 그대로 인용할 수 없다.

이 스크립트는 순수 Python으로 온라인 매칭 로직만 재현해서(DB 불필요) 원래 순서/역순/
여러 random seed 순서로 반복 평가하고, Precision/Recall/F1과 순서에 따른 군집 구성
안정성을 함께 측정한다.

주의: channel/feature_area/submission_status/error_code/window_seconds 필터는
evaluate_embedding_pairs.CASES가 이 차원을 갖고 있지 않아 재현하지 않는다 — 이 평가는
"같은 issue_type 안에서 embedding 유사도만으로 매칭이 얼마나 잘 되는지"만 본다.
"""

import hashlib
import json
from collections import defaultdict
from random import Random

from evaluate_clustering_quality import (
    build_similarity_cache,
    cached_similarity,
    calculate_cluster_metrics,
    partition_report_ids,
)
from evaluate_embedding_pairs import CASES

from app.clustering import SIMILARITY_THRESHOLD
from app.embedding import get_symptom_embedding

ORDER_SEEDS = range(10)
THRESHOLD_CANDIDATES = [value / 100 for value in range(50, 66)]
ADOPTED_THRESHOLD = 0.57  # 스윕 결과로 채택 확정 (2026-09-01), 배치 확정값 0.58 대신 사용


def dataset_fingerprint(cases: list[tuple]) -> str:
    payload = [(case_id, issue_type.value, cluster_label, text) for case_id, issue_type, cluster_label, text in cases]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def cluster_online_incremental(
    reports: list[tuple[str, str, list[float]]],
    cache: dict,
    threshold: float,
) -> frozenset:
    """
    reports는 도착 순서 그대로 정렬된 (report_id, issue_type, embedding) 리스트다.
    services/signals.py의 온라인 매칭(linkage_method=AVERAGE)을 재현한다:
    새 제보 1건과 기존 클러스터 각각의 "전체 멤버 평균 유사도"를 비교해서
    가장 높은 클러스터가 threshold 이상이면 편입, 아니면 새 클러스터를 만든다.
    """
    _, excluded_ids_all = partition_report_ids(reports)
    excluded_ids = set(excluded_ids_all)

    clusters_by_issue_type: dict[str, list[list[str]]] = defaultdict(list)
    excluded_result: list[frozenset] = []

    for report_id, issue_type, _embedding in reports:
        if report_id in excluded_ids:
            excluded_result.append(frozenset({report_id}))
            continue

        candidates = clusters_by_issue_type[issue_type]
        best_index = None
        best_score = -1.0
        for index, cluster in enumerate(candidates):
            scores = [cached_similarity(cache, report_id, member_id) for member_id in cluster]
            average = sum(scores) / len(scores)
            if average > best_score:
                best_score = average
                best_index = index

        if best_index is not None and best_score >= threshold:
            candidates[best_index].append(report_id)
        else:
            candidates.append([report_id])

    result = [
        frozenset(cluster)
        for issue_type in sorted(clusters_by_issue_type)
        for cluster in clusters_by_issue_type[issue_type]
    ]
    result.extend(excluded_result)
    return frozenset(result)


def _merge_clusters_in_place(clusters: list[list[str]], cache: dict, threshold: float) -> None:
    """같은 issue_type 안에서, 두 클러스터의 교차 평균 유사도가 threshold 이상이면 합친다.
    더 합칠 게 없을 때까지 반복한다 (제안 2번: 클러스터-클러스터 병합 허용)."""
    merged_any = True
    while merged_any:
        merged_any = False
        for left_index in range(len(clusters)):
            for right_index in range(left_index + 1, len(clusters)):
                cross_scores = [
                    cached_similarity(cache, left_id, right_id)
                    for left_id in clusters[left_index]
                    for right_id in clusters[right_index]
                ]
                average = sum(cross_scores) / len(cross_scores)
                if average >= threshold:
                    clusters[left_index] = clusters[left_index] + clusters[right_index]
                    del clusters[right_index]
                    merged_any = True
                    break
            if merged_any:
                break


def cluster_online_incremental_with_merge(
    reports: list[tuple[str, str, list[float]]],
    cache: dict,
    threshold: float,
) -> frozenset:
    """cluster_online_incremental과 동일하되, 매 제보 편입 뒤 기존 클러스터끼리도
    합칠 수 있는지 확인한다. 개선안 2번(클러스터-클러스터 병합 허용) 시뮬레이션."""
    _, excluded_ids_all = partition_report_ids(reports)
    excluded_ids = set(excluded_ids_all)

    clusters_by_issue_type: dict[str, list[list[str]]] = defaultdict(list)
    excluded_result: list[frozenset] = []

    for report_id, issue_type, _embedding in reports:
        if report_id in excluded_ids:
            excluded_result.append(frozenset({report_id}))
            continue

        candidates = clusters_by_issue_type[issue_type]
        best_index = None
        best_score = -1.0
        for index, cluster in enumerate(candidates):
            scores = [cached_similarity(cache, report_id, member_id) for member_id in cluster]
            average = sum(scores) / len(scores)
            if average > best_score:
                best_score = average
                best_index = index

        if best_index is not None and best_score >= threshold:
            candidates[best_index].append(report_id)
        else:
            candidates.append([report_id])

        _merge_clusters_in_place(candidates, cache, threshold)

    result = [
        frozenset(cluster)
        for issue_type in sorted(clusters_by_issue_type)
        for cluster in clusters_by_issue_type[issue_type]
    ]
    result.extend(excluded_result)
    return frozenset(result)


def evaluate_order(
    reports: list[tuple[str, str, list[float]]],
    cache: dict,
    case_by_id: dict,
    threshold: float,
    cluster_fn=cluster_online_incremental,
) -> tuple[frozenset, tuple]:
    member_sets = cluster_fn(reports, cache, threshold)
    metrics = calculate_cluster_metrics(member_sets, case_by_id)
    return member_sets, metrics


def sweep_threshold(
    orderings: list[tuple[str, list[tuple[str, str, list[float]]]]],
    cache: dict,
    case_by_id: dict,
    threshold: float,
    cluster_fn=cluster_online_incremental,
) -> dict:
    """orderings 전체(원래/역순/seed N개)에 대해 이 threshold의 평균/최소/최대 성능을 낸다."""
    f1s, precisions, recalls = [], [], []
    unstable_count = 0
    baseline = None
    for _label, reports in orderings:
        member_sets, metrics = evaluate_order(reports, cache, case_by_id, threshold, cluster_fn)
        if baseline is None:
            baseline = member_sets
        elif member_sets != baseline:
            unstable_count += 1
        _tp, _fp, _fn, _tn, precision, recall, f1 = metrics
        f1s.append(f1)
        precisions.append(precision)
        recalls.append(recall)
    return {
        "threshold": threshold,
        "avg_precision": sum(precisions) / len(precisions),
        "avg_recall": sum(recalls) / len(recalls),
        "avg_f1": sum(f1s) / len(f1s),
        "min_f1": min(f1s),
        "max_f1": max(f1s),
        "unstable_count": unstable_count,
    }


def main() -> None:
    print(f"dataset fingerprint: {dataset_fingerprint(CASES)}")
    print(f"적용 threshold: {SIMILARITY_THRESHOLD:.2f}")
    print(f"전체 평가 문장 수: {len(CASES)}\n")

    reports_original = []
    case_by_id = {}
    for index, (case_id, issue_type, cluster_label, symptom) in enumerate(CASES, 1):
        issue_type_value = issue_type.value
        print(
            f"[{index:02d}/{len(CASES)}] embedding: {case_id} / "
            f"{issue_type_value} / {cluster_label or '평가 제외'}"
        )
        embedding = get_symptom_embedding(symptom)
        reports_original.append((case_id, issue_type_value, embedding))
        case_by_id[case_id] = {
            "issue_type": issue_type_value,
            "cluster_label": cluster_label,
            "symptom": symptom,
        }

    cache = build_similarity_cache(reports_original)

    baseline_sets, baseline_metrics = evaluate_order(
        reports_original, cache, case_by_id, SIMILARITY_THRESHOLD
    )

    print("\n=== 원래 순서 (도착 순서 그대로) ===")
    tp, fp, fn, tn, precision, recall, f1 = baseline_metrics
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"Precision={precision:.6f} Recall={recall:.6f} F1={f1:.6f}")

    print("\n=== 역순 ===")
    reversed_sets, reversed_metrics = evaluate_order(
        list(reversed(reports_original)), cache, case_by_id, SIMILARITY_THRESHOLD
    )
    tp, fp, fn, tn, precision, recall, f1 = reversed_metrics
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"Precision={precision:.6f} Recall={recall:.6f} F1={f1:.6f}")
    reversed_stable = reversed_sets == baseline_sets
    print(f"원래 순서와 군집 구성 동일: {reversed_stable}")

    print(f"\n=== random seed {list(ORDER_SEEDS)} 순서 안정성 ===")
    unstable_seeds = []
    seed_f1s = []
    shuffled_orderings = []
    for seed in ORDER_SEEDS:
        shuffled = list(reports_original)
        Random(seed).shuffle(shuffled)
        shuffled_orderings.append((f"seed_{seed}", shuffled))
        shuffled_sets, shuffled_metrics = evaluate_order(
            shuffled, cache, case_by_id, SIMILARITY_THRESHOLD
        )
        seed_f1s.append(shuffled_metrics[6])
        if shuffled_sets != baseline_sets:
            unstable_seeds.append(seed)
        print(
            f"seed={seed}: F1={shuffled_metrics[6]:.6f} "
            f"군집 구성 동일={shuffled_sets == baseline_sets}"
        )

    print("\n=== 종합 ===")
    print(f"원래 순서 F1={baseline_metrics[6]:.6f}")
    print(f"역순 F1={reversed_metrics[6]:.6f} (동일 구성={reversed_stable})")
    print(f"seed 10개 중 F1 최소={min(seed_f1s):.6f} 최대={max(seed_f1s):.6f}")
    print(f"seed 10개 중 원래 구성과 다른 seed 수={len(unstable_seeds)}/{len(list(ORDER_SEEDS))}")
    if unstable_seeds:
        print(f"불안정한 seed: {unstable_seeds}")

    order_invariant = reversed_stable and not unstable_seeds
    print("\n=== 최종 판정 (threshold=0.58 고정) ===")
    print(
        "온라인 알고리즘은 입력 순서에 "
        + ("영향받지 않았습니다 (order-invariant)." if order_invariant else "영향받았습니다 (order-dependent).")
    )
    if not order_invariant:
        print(
            "→ 배치 평가 결과(Precision 0.972/Recall 0.700/F1 0.814)를 온라인 워커 "
            "성능으로 그대로 인용하면 안 됩니다. 위 순서별 F1 범위를 실제 온라인 지표로 "
            "별도 문서화해야 합니다."
        )

    orderings = [("original", reports_original), ("reversed", list(reversed(reports_original)))]
    orderings.extend(shuffled_orderings)

    print(f"\n=== threshold 스윕 (순서 {len(orderings)}개 평균/최소/최대) ===")
    print("threshold  avg_precision  avg_recall   avg_F1     min_F1     max_F1")
    sweep_results = []
    for threshold in THRESHOLD_CANDIDATES:
        stats = sweep_threshold(orderings, cache, case_by_id, threshold)
        sweep_results.append(stats)
        print(
            f"{threshold:>9.2f} {stats['avg_precision']:>14.6f} {stats['avg_recall']:>11.6f} "
            f"{stats['avg_f1']:>9.6f} {stats['min_f1']:>10.6f} {stats['max_f1']:>10.6f}"
        )

    precision_safe = [row for row in sweep_results if row["avg_precision"] >= 0.80]
    best = (
        max(precision_safe, key=lambda row: (row["avg_f1"], row["min_f1"], -row["threshold"]))
        if precision_safe
        else max(sweep_results, key=lambda row: (row["avg_f1"], row["min_f1"]))
    )
    print("\n=== 온라인 알고리즘 기준 자동 선정 threshold ===")
    if precision_safe:
        print("(평균 precision 0.80 이상 후보 중 avg_F1 최고)")
    else:
        print("(평균 precision 0.80 이상 후보 없음 — 전체 중 avg_F1 최고로 선정)")
    print(
        f"threshold={best['threshold']:.2f}, avg_precision={best['avg_precision']:.6f}, "
        f"avg_recall={best['avg_recall']:.6f}, avg_F1={best['avg_f1']:.6f}, "
        f"min_F1={best['min_f1']:.6f}, max_F1={best['max_f1']:.6f}"
    )
    print(
        f"\n현재 사용 중인 threshold={SIMILARITY_THRESHOLD:.2f} (배치 기준 확정값)와 "
        f"비교해서, 온라인 기준으로 다른 값이 더 나은지 이 표로 판단하시면 됩니다."
    )

    print(f"\n=== 개선안 2번 검증: 클러스터-클러스터 병합 허용 (threshold={ADOPTED_THRESHOLD:.2f}) ===")
    without_merge = sweep_threshold(
        orderings, cache, case_by_id, ADOPTED_THRESHOLD, cluster_fn=cluster_online_incremental
    )
    with_merge = sweep_threshold(
        orderings,
        cache,
        case_by_id,
        ADOPTED_THRESHOLD,
        cluster_fn=cluster_online_incremental_with_merge,
    )
    print("                avg_precision  avg_recall   avg_F1     min_F1     max_F1   불안정 순서 수")
    print(
        f"병합 없음(현재)  {without_merge['avg_precision']:>13.6f} {without_merge['avg_recall']:>11.6f} "
        f"{without_merge['avg_f1']:>9.6f} {without_merge['min_f1']:>10.6f} {without_merge['max_f1']:>10.6f} "
        f"{without_merge['unstable_count']:>6}/{len(orderings) - 1}"
    )
    print(
        f"병합 허용(제안)  {with_merge['avg_precision']:>13.6f} {with_merge['avg_recall']:>11.6f} "
        f"{with_merge['avg_f1']:>9.6f} {with_merge['min_f1']:>10.6f} {with_merge['max_f1']:>10.6f} "
        f"{with_merge['unstable_count']:>6}/{len(orderings) - 1}"
    )
    f1_gain = with_merge["avg_f1"] - without_merge["avg_f1"]
    min_f1_gain = with_merge["min_f1"] - without_merge["min_f1"]
    print(f"\navg_F1 변화: {f1_gain:+.6f}")
    print(f"min_F1(최악의 경우) 변화: {min_f1_gain:+.6f}")
    print(
        "→ 클러스터 병합 허용이 실제로 순서 안정성/성능을 개선하는지 위 수치로 판단하시면 됩니다."
    )


if __name__ == "__main__":
    main()
