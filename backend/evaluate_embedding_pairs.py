from collections import Counter
from itertools import combinations
from statistics import mean

from app.codes import IssueType
from app.clustering import SIMILARITY_THRESHOLD, cosine_similarity
from app.embedding import get_symptom_embedding


# issue_type은 상위 오류 분류, cluster_label은 실제 의미 군집 정답이다.
# UNKNOWN/UNRELATED는 의미 군집 정답이 없으므로 평가에서 제외한다.
CASES = [
    ("submission_01", IssueType.ORDER_SUBMISSION_FAILURE, "ORDER_SCREEN_STUCK", "주문 버튼을 누른 뒤 로딩이 멈춤"),
    ("submission_02", IssueType.ORDER_SUBMISSION_FAILURE, "ORDER_SCREEN_STUCK", "매도 확인 후 다음 화면으로 넘어가지 않음"),
    ("submission_03", IssueType.ORDER_SUBMISSION_FAILURE, "ORDER_SCREEN_STUCK", "주문 접수 단계에서 계속 대기 상태가 표시됨"),
    ("submission_04", IssueType.ORDER_SUBMISSION_FAILURE, "ORDER_SCREEN_STUCK", "확인 버튼을 눌러도 주문 화면이 반응하지 않음"),
    ("submission_05", IssueType.ORDER_SUBMISSION_FAILURE, "ORDER_SCREEN_STUCK", "주문을 제출하려고 하면 화면이 멈춤"),
    ("result_01", IssueType.ORDER_RESULT_UNCONFIRMED, "ORDER_RESULT_MISSING", "매도 주문 후 체결 여부를 확인할 수 없음"),
    ("result_02", IssueType.ORDER_RESULT_UNCONFIRMED, "ORDER_RESULT_MISSING", "주문을 넣었지만 접수되었는지 알 수 없음"),
    ("result_03", IssueType.ORDER_RESULT_UNCONFIRMED, "ORDER_RESULT_MISSING", "주문 완료 화면이 나오지 않아 접수 여부를 알 수 없음"),
    ("result_04", IssueType.ORDER_RESULT_UNCONFIRMED, "ORDER_RESULT_MISSING", "주문번호가 나타나지 않아 주문 결과가 불확실함"),
    ("result_05", IssueType.ORDER_RESULT_UNCONFIRMED, "ORDER_RESULT_MISSING", "주문을 시도했으나 처리 결과를 확인하지 못함"),
    ("login_01", IssueType.LOGIN_ACCESS_FAILURE, "LOGIN_CREDENTIAL_FAILURE", "비밀번호를 입력해도 로그인이 실패함"),
    ("login_02", IssueType.LOGIN_ACCESS_FAILURE, "APP_LOGIN_FAILURE", "M-able 앱에 로그인이 되지 않음"),
    ("login_03", IssueType.LOGIN_ACCESS_FAILURE, "AUTH_CODE_MISSING", "인증번호가 오지 않아 로그인할 수 없음"),
    ("login_04", IssueType.LOGIN_ACCESS_FAILURE, "LOGIN_CREDENTIAL_FAILURE", "계정 비밀번호 오류로 접속이 거부됨"),
    ("login_05", IssueType.LOGIN_ACCESS_FAILURE, "LOGIN_CREDENTIAL_FAILURE", "비밀번호가 올바른데도 계정 로그인이 거절됨"),
    ("login_06", IssueType.LOGIN_ACCESS_FAILURE, "APP_LOGIN_FAILURE", "앱 로그인 화면에서 접속이 계속 실패함"),
    ("login_07", IssueType.LOGIN_ACCESS_FAILURE, "APP_LOGIN_FAILURE", "M-able 로그인 버튼을 눌러도 접속되지 않음"),
    ("login_08", IssueType.LOGIN_ACCESS_FAILURE, "AUTH_CODE_MISSING", "로그인 인증 문자가 휴대전화로 오지 않음"),
    ("login_09", IssueType.LOGIN_ACCESS_FAILURE, "AUTH_CODE_MISSING", "본인인증 번호를 받지 못해 로그인 단계에서 막힘"),
    ("balance_01", IssueType.BALANCE_INQUIRY_ERROR, "BALANCE_NOT_REFRESHED", "잔고 화면이 갱신되지 않음"),
    ("balance_02", IssueType.BALANCE_INQUIRY_ERROR, "BALANCE_NOT_REFRESHED", "보유 주식 수량이 잔고에 표시되지 않음"),
    ("balance_03", IssueType.BALANCE_INQUIRY_ERROR, "EXECUTION_HISTORY_EMPTY", "체결 내역을 조회하면 빈 화면이 나타남"),
    ("balance_04", IssueType.BALANCE_INQUIRY_ERROR, "ORDER_HISTORY_ERROR", "주문 내역 조회 화면에서 오류가 발생함"),
    ("balance_05", IssueType.BALANCE_INQUIRY_ERROR, "BALANCE_NOT_REFRESHED", "보유 종목과 수량이 잔고 화면에 갱신되지 않음"),
    ("balance_06", IssueType.BALANCE_INQUIRY_ERROR, "EXECUTION_HISTORY_EMPTY", "체결 목록을 열어도 거래 기록이 보이지 않음"),
    ("balance_07", IssueType.BALANCE_INQUIRY_ERROR, "EXECUTION_HISTORY_EMPTY", "체결 내역 화면에 아무 항목도 표시되지 않음"),
    ("balance_08", IssueType.BALANCE_INQUIRY_ERROR, "ORDER_HISTORY_ERROR", "주문내역 메뉴를 조회하면 오류 화면이 나타남"),
    ("balance_09", IssueType.BALANCE_INQUIRY_ERROR, "ORDER_HISTORY_ERROR", "과거 주문 기록을 불러오는 중 조회 오류가 발생함"),
    ("network_01", IssueType.DEVICE_NETWORK_SUSPECTED, "WIFI_CONNECTION_FAILURE", "와이파이 연결이 끊길 때 주문 화면이 멈춤"),
    ("network_02", IssueType.DEVICE_NETWORK_SUSPECTED, "MOBILE_DATA_FAILURE", "모바일 데이터가 불안정하면 앱 접속이 실패함"),
    ("network_03", IssueType.DEVICE_NETWORK_SUSPECTED, "DEVICE_SPECIFIC_FAILURE", "내 휴대전화에서만 M-able 화면이 열리지 않음"),
    ("network_04", IssueType.DEVICE_NETWORK_SUSPECTED, "NETWORK_ERROR_MESSAGE", "네트워크 연결 오류 메시지가 표시됨"),
    ("network_05", IssueType.DEVICE_NETWORK_SUSPECTED, "WIFI_CONNECTION_FAILURE", "와이파이가 끊어진 뒤 주문 화면이 응답하지 않음"),
    ("network_06", IssueType.DEVICE_NETWORK_SUSPECTED, "WIFI_CONNECTION_FAILURE", "무선 인터넷 연결이 끊기면 앱 화면이 멈춤"),
    ("network_07", IssueType.DEVICE_NETWORK_SUSPECTED, "MOBILE_DATA_FAILURE", "LTE 연결이 불안정할 때 앱에 접속할 수 없음"),
    ("network_08", IssueType.DEVICE_NETWORK_SUSPECTED, "MOBILE_DATA_FAILURE", "모바일 네트워크 상태가 나쁘면 로그인에 실패함"),
    ("network_09", IssueType.DEVICE_NETWORK_SUSPECTED, "DEVICE_SPECIFIC_FAILURE", "다른 기기에서는 되지만 내 스마트폰에서만 앱이 실행되지 않음"),
    ("network_10", IssueType.DEVICE_NETWORK_SUSPECTED, "DEVICE_SPECIFIC_FAILURE", "특정 휴대전화에서만 M-able 접속 화면이 열리지 않음"),
    ("network_11", IssueType.DEVICE_NETWORK_SUSPECTED, "NETWORK_ERROR_MESSAGE", "앱에 네트워크 접속 오류 안내가 나타남"),
    ("network_12", IssueType.DEVICE_NETWORK_SUSPECTED, "NETWORK_ERROR_MESSAGE", "통신 연결 실패 메시지가 표시되어 진행할 수 없음"),
    ("unrelated_01", IssueType.UNRELATED_OR_AMBIGUOUS, None, "해외주식 거래 수수료가 궁금함"),
    ("unrelated_02", IssueType.UNRELATED_OR_AMBIGUOUS, None, "공모주 청약 일정을 확인하고 싶음"),
    ("unrelated_03", IssueType.UNRELATED_OR_AMBIGUOUS, None, "오늘 증시 전망을 알려 달라는 문의"),
    ("unknown_01", IssueType.UNKNOWN, None, "MTS 앱에서 알 수 없는 오류가 발생함"),
    ("unknown_02", IssueType.UNKNOWN, None, "기능을 사용하려고 했는데 제대로 되지 않음"),
    ("unknown_03", IssueType.UNKNOWN, None, "M-able 사용 중 문제가 발생함"),
]

THRESHOLD = SIMILARITY_THRESHOLD
THRESHOLD_CANDIDATES = [value / 100 for value in range(65, 86)]


def describe(scores):
    if not scores:
        return "count=0"
    return (
        f"count={len(scores)} min={min(scores):.6f} "
        f"mean={mean(scores):.6f} max={max(scores):.6f}"
    )


def print_pair(item):
    similarity, left, right = item
    left_id, left_type, left_cluster, left_text = left
    right_id, right_type, right_cluster, right_text = right
    print(
        f"{similarity:.6f} / "
        f"{left_id}({left_type.value}, {left_cluster}) ↔ "
        f"{right_id}({right_type.value}, {right_cluster})"
    )
    print(f"  L: {left_text}")
    print(f"  R: {right_text}")


def calculate_metrics(pair_results, threshold):
    tp = fp = fn = tn = 0

    for similarity, actual_same in pair_results:
        predicted_same = similarity >= threshold
        if predicted_same and actual_same:
            tp += 1
        elif predicted_same:
            fp += 1
        elif actual_same:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return tp, fp, fn, tn, precision, recall, f1


def main():
    vectors = {}
    cluster_counts = Counter(
        cluster_label
        for _, _, cluster_label, _ in CASES
        if cluster_label is not None
    )

    print(f"평가 문장 수: {len(CASES)}")
    print(f"적용 threshold: {THRESHOLD:.2f}\n")
    print("=== 의미 군집별 평가 문장 수 ===")
    for cluster_label, count in sorted(cluster_counts.items()):
        print(f"{cluster_label}: {count}")
    print()

    for index, (case_id, issue_type, cluster_label, text) in enumerate(CASES, 1):
        label = cluster_label if cluster_label is not None else "평가 제외"
        print(
            f"[{index:02d}/{len(CASES)}] embedding: "
            f"{case_id} / {issue_type.value} / {label}"
        )
        vectors[case_id] = get_symptom_embedding(text)

    same_cluster_scores = []
    different_cluster_scores = []
    false_positives = []
    false_negatives = []
    excluded_cross_type = []
    excluded_noise_count = 0
    eligible_pair_results = []
    tp = fp = fn = tn = 0

    for left, right in combinations(CASES, 2):
        left_id, left_type, left_cluster, _ = left
        right_id, right_type, right_cluster, _ = right
        similarity = cosine_similarity(vectors[left_id], vectors[right_id])

        if left_cluster is None or right_cluster is None:
            excluded_noise_count += 1
            continue

        # 운영 군집에서도 서로 다른 오류유형은 먼저 차단할 대상이다.
        if left_type != right_type:
            excluded_cross_type.append((similarity, left, right))
            continue

        actual_same = left_cluster == right_cluster
        predicted_same = similarity >= THRESHOLD
        eligible_pair_results.append((similarity, actual_same))

        if actual_same:
            same_cluster_scores.append(similarity)
        else:
            different_cluster_scores.append(similarity)

        if predicted_same and actual_same:
            tp += 1
        elif predicted_same:
            fp += 1
            false_positives.append((similarity, left, right))
        elif actual_same:
            fn += 1
            false_negatives.append((similarity, left, right))
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    print("\n=== 의미 군집 유사도 분포 (같은 오류유형 내부만) ===")
    print(f"같은 의미 군집: {describe(same_cluster_scores)}")
    print(f"다른 의미 군집: {describe(different_cluster_scores)}")

    print("\n=== Pairwise 평가 (오류유형 gate 적용) ===")
    print(f"TP={tp}")
    print(f"FP={fp}")
    print(f"FN={fn}")
    print(f"TN={tn}")
    print(f"Precision={precision:.6f}")
    print(f"Recall={recall:.6f}")
    print(f"F1={f1:.6f}")
    print(f"다른 오류유형이라 제외된 pair={len(excluded_cross_type)}")
    print(f"UNKNOWN/UNRELATED 포함으로 제외된 pair={excluded_noise_count}")

    print("\n=== threshold 자동 비교 (같은 오류유형 내부만) ===")
    print("threshold   TP  FP  FN  TN  precision  recall     F1")
    comparison_results = []
    for threshold in THRESHOLD_CANDIDATES:
        result = calculate_metrics(eligible_pair_results, threshold)
        c_tp, c_fp, c_fn, c_tn, c_precision, c_recall, c_f1 = result
        comparison_results.append((threshold, *result))
        print(
            f"{threshold:>9.2f}  {c_tp:>3} {c_fp:>3} {c_fn:>3} {c_tn:>3}  "
            f"{c_precision:>9.6f}  {c_recall:>6.6f}  {c_f1:>6.6f}"
        )

    best_f1 = max(
        comparison_results,
        key=lambda row: (row[7], row[5], row[6], row[0]),
    )
    precision_candidates = [
        row for row in comparison_results if row[5] >= 0.80
    ]
    best_precision_safe = (
        max(precision_candidates, key=lambda row: (row[7], row[6], row[0]))
        if precision_candidates
        else None
    )

    print("\n=== threshold 후보 ===")
    print(
        f"최고 F1: threshold={best_f1[0]:.2f}, "
        f"precision={best_f1[5]:.6f}, recall={best_f1[6]:.6f}, "
        f"F1={best_f1[7]:.6f}"
    )
    if best_precision_safe is not None:
        print(
            f"Precision 0.80 이상 중 최고 F1: "
            f"threshold={best_precision_safe[0]:.2f}, "
            f"precision={best_precision_safe[5]:.6f}, "
            f"recall={best_precision_safe[6]:.6f}, "
            f"F1={best_precision_safe[7]:.6f}"
        )

    print("\n=== 같은 오류유형 내부 false positive ===")
    if false_positives:
        for item in sorted(false_positives, reverse=True)[:10]:
            print_pair(item)
    else:
        print("없음")

    print("\n=== threshold를 통과하지 못한 같은 의미 군집 pair ===")
    if false_negatives:
        for item in sorted(false_negatives)[:10]:
            print_pair(item)
    else:
        print("없음")

    print("\n=== 오류유형 gate가 차단한 고유사도 pair 상위 10개 (참고) ===")
    for item in sorted(excluded_cross_type, reverse=True)[:10]:
        print_pair(item)


if __name__ == "__main__":
    main()
