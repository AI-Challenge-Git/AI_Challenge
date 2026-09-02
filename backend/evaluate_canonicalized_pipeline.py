"""
canonicalization 적용 후 실제 파이프라인 기준 재평가.

evaluate_canonicalization_ceiling.py는 "AI가 완벽하게 분류했다면"이라는 가정(ceiling)
이었다. 이 스크립트는 실제로 RealDualExtractor를 통과시켜서 나온 결과(AI가 실제로
판단한 issue_type, AI가 실제로 고른 canonical symptom)를 그대로 써서 진짜 온라인/배치
성능을 측정한다.

기존 evaluate_clustering_quality.py / evaluate_online_clustering_quality.py는 CASES의
symptom 원문을 직접 임베딩했다. 이 스크립트는 그 대신:
1. 각 케이스의 symptom 원문을 masked_text로 보고 RealDualExtractor.extract_safe() 호출
2. AI가 실제로 판단한 issue_type과 canonical symptom(taxonomy 적용됨)을 가져옴
3. 그 canonical symptom을 임베딩해서 배치/온라인 클러스터링 재평가

issue_type 자체가 AI 판단과 CASES 정답이 다를 수 있는데, 이것도 실제 운영에서 일어나는
일이므로 AI가 실제로 판단한 issue_type을 그대로 클러스터링에 사용한다 (파이프라인
전체의 정직한 성능 측정).

이 결과가 기존 원문 기준 결과(배치 F1=0.814, 온라인 평균 F1≈0.72)와 다르다는 것 자체가,
symptom 텍스트 분포가 바뀌었다는 증거다 — 그래서 새 model_revision/policy version으로
등록하고 재평가해야 한다는 게 근거를 갖게 된다.
"""

from evaluate_clustering_quality import (
    build_similarity_cache,
    calculate_cluster_metrics,
    cluster_agglomerative_candidate,
)
from evaluate_embedding_pairs import CASES
from evaluate_issue_types import normalized_issue_type
from evaluate_online_clustering_quality import (
    ORDER_SEEDS,
    THRESHOLD_CANDIDATES,
    cluster_online_incremental,
    sweep_threshold,
)
from random import Random

from app.clustering import SIMILARITY_THRESHOLD
from app.embedding import get_symptom_embedding
from app.real_extractor_v5 import RealDualExtractor

BATCH_THRESHOLD_CANDIDATES = [value / 100 for value in range(50, 96)]


def main() -> None:
    extractor = RealDualExtractor()

    reports: list[tuple[str, str, list[float]]] = []
    case_by_id: dict[str, dict] = {}
    mismatches: list[tuple[str, str, str, str, str]] = []
    extraction_failures = 0

    print(f"{len(CASES)}건 실제 추출(issue_type+canonical symptom) 진행 중...\n")
    for index, (case_id, expected_issue_type, cluster_label, symptom_text) in enumerate(CASES, 1):
        outcome = extractor.extract_safe(symptom_text)
        if outcome.result is None:
            print(f"[{index:02d}/{len(CASES)}] {case_id}: 추출 실패 ({outcome.failure_reason})")
            extraction_failures += 1
            continue

        issue_type_field = outcome.result.technical.issue_type
        actual_issue_type = normalized_issue_type(issue_type_field.value, issue_type_field.status)
        symptom_field = outcome.result.technical.symptom
        canonical_symptom = symptom_field.value or symptom_text

        if actual_issue_type != expected_issue_type.value:
            mismatches.append(
                (case_id, expected_issue_type.value, actual_issue_type, symptom_text, canonical_symptom)
            )

        print(
            f"[{index:02d}/{len(CASES)}] {case_id}: issue_type={actual_issue_type} "
            f"symptom='{canonical_symptom}'"
        )

        embedding = get_symptom_embedding(canonical_symptom)
        reports.append((case_id, actual_issue_type, embedding))
        case_by_id[case_id] = {
            "issue_type": actual_issue_type,
            "cluster_label": cluster_label,
            "symptom": canonical_symptom,
        }

    print(f"\n추출 실패: {extraction_failures}/{len(CASES)}")

    print("\n=== issue_type이 CASES 정답과 다르게 판단된 케이스 ===")
    if not mismatches:
        print("없음")
    else:
        for case_id, expected, actual, orig, canon in mismatches:
            print(f"{case_id}: 기대={expected} 실제={actual}")
            print(f"  원문='{orig}'")
            print(f"  canonical symptom='{canon}'")

    cache = build_similarity_cache(reports)

    print("\n=== 배치(average-linkage), canonical symptom 기준 ===")
    print("threshold  Precision  Recall     F1")
    batch_rows = []
    for threshold in BATCH_THRESHOLD_CANDIDATES:
        member_sets = cluster_agglomerative_candidate(reports, cache, threshold, linkage="average")
        metrics = calculate_cluster_metrics(member_sets, case_by_id)
        batch_rows.append((threshold, *metrics))
        _tp, _fp, _fn, _tn, precision, recall, f1 = metrics
        print(f"{threshold:>9.2f} {precision:>10.6f} {recall:>10.6f} {f1:>8.6f}")
    best_batch = max(batch_rows, key=lambda row: row[7])
    print(
        f"\n최고 F1: threshold={best_batch[0]:.2f}, Precision={best_batch[5]:.6f}, "
        f"Recall={best_batch[6]:.6f}, F1={best_batch[7]:.6f}"
    )
    print("(원문 기준 배치 결과: F1=0.814 @ threshold=0.58)")

    print(f"\n=== 온라인(incremental), canonical symptom 기준, threshold {SIMILARITY_THRESHOLD:.2f} ===")
    baseline_sets = cluster_online_incremental(reports, cache, SIMILARITY_THRESHOLD)
    baseline_metrics = calculate_cluster_metrics(baseline_sets, case_by_id)
    _tp, _fp, _fn, _tn, precision, recall, f1 = baseline_metrics
    print(f"원래 순서: Precision={precision:.6f} Recall={recall:.6f} F1={f1:.6f}")

    reversed_sets = cluster_online_incremental(list(reversed(reports)), cache, SIMILARITY_THRESHOLD)
    reversed_metrics = calculate_cluster_metrics(reversed_sets, case_by_id)
    print(f"역순: F1={reversed_metrics[6]:.6f} (구성 동일={reversed_sets == baseline_sets})")

    seed_f1s = []
    unstable = 0
    orderings = [("original", reports), ("reversed", list(reversed(reports)))]
    for seed in ORDER_SEEDS:
        shuffled = list(reports)
        Random(seed).shuffle(shuffled)
        orderings.append((f"seed_{seed}", shuffled))
        shuffled_sets = cluster_online_incremental(shuffled, cache, SIMILARITY_THRESHOLD)
        shuffled_metrics = calculate_cluster_metrics(shuffled_sets, case_by_id)
        seed_f1s.append(shuffled_metrics[6])
        if shuffled_sets != baseline_sets:
            unstable += 1

    print(f"seed 10개: F1 최소={min(seed_f1s):.6f} 최대={max(seed_f1s):.6f}, 불안정={unstable}/10")
    print("(원문 기준 온라인 결과: 평균 F1≈0.72, 범위 0.525~0.851, 12/12 불안정)")

    print(f"\n=== threshold 스윕 (온라인, canonical symptom, 순서 {len(orderings)}개 평균) ===")
    print("threshold  avg_precision  avg_recall   avg_F1     min_F1     max_F1   불안정")
    sweep_results = []
    for threshold in THRESHOLD_CANDIDATES:
        stats = sweep_threshold(orderings, cache, case_by_id, threshold)
        sweep_results.append(stats)
        print(
            f"{threshold:>9.2f} {stats['avg_precision']:>14.6f} {stats['avg_recall']:>11.6f} "
            f"{stats['avg_f1']:>9.6f} {stats['min_f1']:>10.6f} {stats['max_f1']:>10.6f} "
            f"{stats['unstable_count']:>6}/{len(orderings) - 1}"
        )

    precision_safe = [row for row in sweep_results if row["avg_precision"] >= 0.80]
    best = (
        max(precision_safe, key=lambda row: (row["avg_f1"], row["min_f1"], -row["threshold"]))
        if precision_safe
        else max(sweep_results, key=lambda row: (row["avg_f1"], row["min_f1"]))
    )
    print("\n=== canonical symptom 기준 자동 선정 threshold ===")
    print(
        f"threshold={best['threshold']:.2f}, avg_precision={best['avg_precision']:.6f}, "
        f"avg_recall={best['avg_recall']:.6f}, avg_F1={best['avg_f1']:.6f}, "
        f"min_F1={best['min_f1']:.6f}, max_F1={best['max_f1']:.6f}, "
        f"불안정={best['unstable_count']}/{len(orderings) - 1}"
    )


if __name__ == "__main__":
    main()
