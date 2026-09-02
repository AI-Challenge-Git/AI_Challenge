"""
Canonicalization 상한선(ceiling) 테스트.

지금은 고객이 쓴 원문 symptom을 그대로 임베딩한다. 표현이 달라도 같은 의미면
"AI가 완벽하게 정해진 카테고리(canonical label)로 정리한 뒤 그 카테고리 텍스트를
임베딩했다면 클러스터링이 얼마나 좋아질까?"를 미리 확인한다.

evaluate_embedding_pairs.CASES에는 이미 정답 cluster_label(예: ORDER_SCREEN_STUCK)이
있다. 이 라벨마다 대표 한국어 문구를 하나씩 만들어서, 각 케이스의 symptom 대신
그 문구를 임베딩하고 배치(average-linkage)·온라인 알고리즘 둘 다 재평가한다.

이건 "AI가 이 정도로 정확하게 분류한다면"이라는 상한선(ceiling)이지, 실제 AI
canonicalization 프롬프트의 성능이 아니다. 상한선이 낮으면 이 방향 자체가
가치가 없다는 뜻이고, 높으면 canonicalization 프롬프트를 실제로 만들 가치가
있다는 뜻이다.
"""

from evaluate_clustering_quality import (
    build_similarity_cache,
    calculate_cluster_metrics,
    cluster_agglomerative_candidate,
)
from evaluate_embedding_pairs import CASES
from evaluate_online_clustering_quality import cluster_online_incremental

from app.clustering import SIMILARITY_THRESHOLD
from app.embedding import get_symptom_embedding

# cluster_label -> 대표 canonical 문구. AI가 완벽하게 이 카테고리로 분류했다고 가정.
CANONICAL_PHRASES = {
    "APP_LOGIN_FAILURE": "앱 로그인이 되지 않음",
    "AUTH_CODE_MISSING": "인증번호가 오지 않음",
    "BALANCE_NOT_REFRESHED": "잔고가 갱신되지 않음",
    "DEVICE_SPECIFIC_FAILURE": "특정 기기에서만 실행되지 않음",
    "EXECUTION_HISTORY_EMPTY": "체결 내역이 비어 있음",
    "LOGIN_CREDENTIAL_FAILURE": "로그인 정보 오류로 접속 실패",
    "MOBILE_DATA_FAILURE": "모바일 데이터 연결에서 실패",
    "NETWORK_ERROR_MESSAGE": "네트워크 오류 메시지가 표시됨",
    "ORDER_HISTORY_ERROR": "주문 내역 조회 오류",
    "ORDER_RESULT_MISSING": "주문 결과를 확인할 수 없음",
    "ORDER_SCREEN_STUCK": "주문 화면이 멈춤",
    "WIFI_CONNECTION_FAILURE": "와이파이 연결 실패",
}

THRESHOLD_CANDIDATES = [value / 100 for value in range(50, 96)]


def main() -> None:
    missing_labels = {
        cluster_label
        for _case_id, _issue_type, cluster_label, _symptom in CASES
        if cluster_label is not None
    } - set(CANONICAL_PHRASES)
    if missing_labels:
        raise SystemExit(f"CANONICAL_PHRASES에 없는 라벨: {sorted(missing_labels)}")

    print(f"고유 canonical 문구 {len(CANONICAL_PHRASES)}개 임베딩 중...")
    embedding_by_label = {
        label: get_symptom_embedding(phrase) for label, phrase in CANONICAL_PHRASES.items()
    }
    print("완료.\n")

    reports = []
    case_by_id = {}
    for case_id, issue_type, cluster_label, symptom in CASES:
        issue_type_value = issue_type.value
        if cluster_label is None:
            # UNKNOWN/UNRELATED처럼 애초에 canonical 카테고리가 없는 케이스는
            # 원문 그대로 임베딩한다 (canonicalization 대상이 아님).
            embedding = get_symptom_embedding(symptom)
        else:
            embedding = embedding_by_label[cluster_label]
        reports.append((case_id, issue_type_value, embedding))
        case_by_id[case_id] = {
            "issue_type": issue_type_value,
            "cluster_label": cluster_label,
            "symptom": symptom,
        }

    cache = build_similarity_cache(reports)

    print("=== 배치(average-linkage) ceiling ===")
    print("threshold  Precision  Recall     F1")
    batch_rows = []
    for threshold in THRESHOLD_CANDIDATES:
        member_sets = cluster_agglomerative_candidate(reports, cache, threshold, linkage="average")
        metrics = calculate_cluster_metrics(member_sets, case_by_id)
        batch_rows.append((threshold, *metrics))
        _tp, _fp, _fn, _tn, precision, recall, f1 = metrics
        print(f"{threshold:>9.2f} {precision:>10.6f} {recall:>10.6f} {f1:>8.6f}")

    best_batch = max(batch_rows, key=lambda row: row[6])
    print(
        f"\n최고 F1: threshold={best_batch[0]:.2f}, "
        f"Precision={best_batch[4]:.6f}, Recall={best_batch[5]:.6f}, F1={best_batch[6]:.6f}"
    )
    print(f"(현재 원문 기준 배치 F1=0.814, threshold={SIMILARITY_THRESHOLD:.2f}과 비교)")

    print("\n=== 온라인(incremental) ceiling, threshold=0.58 고정 ===")
    member_sets = cluster_online_incremental(reports, cache, SIMILARITY_THRESHOLD)
    metrics = calculate_cluster_metrics(member_sets, case_by_id)
    _tp, _fp, _fn, _tn, precision, recall, f1 = metrics
    print(f"Precision={precision:.6f} Recall={recall:.6f} F1={f1:.6f}")
    print("(현재 원문 기준 온라인 평균 F1≈0.720과 비교)")


if __name__ == "__main__":
    main()
