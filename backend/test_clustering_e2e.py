from datetime import UTC, datetime

from app.clustering import cosine_similarity, group_similar_reports
from app.embedding import get_symptom_embedding
from app.group_summary import (
    get_issue_type_distribution,
    summarize_group_issue_type,
    summarize_group_symptoms,
)

reports = [
    (
        "r1",
        "ORDER_SUBMISSION_FAILURE",
        "주문 버튼을 누른 뒤 로딩이 멈춤",
    ),
    (
        "r2",
        "ORDER_SUBMISSION_FAILURE",
        "주문 버튼을 누르면 로딩 화면에서 멈춤",
    ),
    (
        "r3",
        "ORDER_RESULT_UNCONFIRMED",
        "주문 후 로딩이 멈춰 결과를 확인할 수 없음",
    ),
    ("r4", "UNKNOWN", "앱에서 알 수 없는 오류가 발생함"),
    ("r5", "UNKNOWN", "앱에서 알 수 없는 오류가 발생함"),
    ("r6", "UNRELATED_OR_AMBIGUOUS", "해외주식 수수료가 궁금함"),
    (
        "r7",
        "UNRELATED_OR_AMBIGUOUS",
        "해외주식 거래 수수료를 알고 싶음",
    ),
]

# 각 증상은 한 번만 임베딩한다.
embeddings = [
    (report_id, issue_type, get_symptom_embedding(symptom))
    for report_id, issue_type, symptom in reports
]

embedding_by_id = {
    report_id: embedding
    for report_id, _, embedding in embeddings
}

print("Pairwise cosine similarity:")
for left_index in range(len(reports)):
    for right_index in range(left_index + 1, len(reports)):
        left_id, left_issue_type, left_symptom = reports[left_index]
        right_id, right_issue_type, right_symptom = reports[right_index]

        similarity = cosine_similarity(
            embedding_by_id[left_id],
            embedding_by_id[right_id],
        )

        same_issue_type = left_issue_type == right_issue_type

        print(
            f"  {left_id} ↔ {right_id}: "
            f"{similarity:.6f} "
            f"(same_issue_type={same_issue_type})"
        )
        print(f"    {left_id}: {left_symptom}")
        print(f"    {right_id}: {right_symptom}")

groups = group_similar_reports(
    embeddings,
    now=datetime.now(UTC),
)

group_members_by_id = {}
for group in groups:
    member_ids = frozenset(
        member.technical_symptom_id
        for member in group
    )
    for member_id in member_ids:
        group_members_by_id[member_id] = member_ids

assert group_members_by_id["r1"] == group_members_by_id["r2"], (
    "같은 오류유형의 고유사도 증상인 r1과 r2가 같은 그룹이어야 합니다."
)
assert group_members_by_id["r3"] != group_members_by_id["r1"], (
    "오류유형이 다른 r3은 r1/r2 그룹과 분리되어야 합니다."
)
assert group_members_by_id["r4"] == frozenset({"r4"}), (
    "UNKNOWN인 r4는 단독 그룹이어야 합니다."
)
assert group_members_by_id["r5"] == frozenset({"r5"}), (
    "UNKNOWN인 r5는 동일 문장이어도 단독 그룹이어야 합니다."
)
assert group_members_by_id["r6"] == frozenset({"r6"}), (
    "UNRELATED_OR_AMBIGUOUS인 r6은 단독 그룹이어야 합니다."
)
assert group_members_by_id["r7"] == frozenset({"r7"}), (
    "UNRELATED_OR_AMBIGUOUS인 r7은 유사 문장이어도 단독 그룹이어야 합니다."
)

print()
print("군집화 결과:")
for group in groups:
    ids_in_group = {
        member.technical_symptom_id
        for member in group
    }

    group_issue_types = [
        issue_type
        for report_id, issue_type, _ in reports
        if report_id in ids_in_group
    ]
    group_symptoms = [
        (symptom, embedding_by_id[report_id])
        for report_id, _, symptom in reports
        if report_id in ids_in_group
    ]

    print(f"  그룹 (대표: {group[0].technical_symptom_id}):")
    print(
        "    멤버:",
        [member.technical_symptom_id for member in group],
    )
    print(
        "    similarity:",
        [round(member.similarity, 6) for member in group],
    )
    print(
        "    대표 오류유형:",
        summarize_group_issue_type(group_issue_types),
    )
    print(
        "    오류유형 분포:",
        get_issue_type_distribution(group_issue_types),
    )
    print(
        "    대표 증상:",
        summarize_group_symptoms(group_symptoms),
    )

print()
assert summarize_group_symptoms([]) == "증상 정보 없음"
assert summarize_group_symptoms([
    ("단일 증상", [1.0, 0.0]),
]) == "단일 증상"

medoid_test_symptoms = [
    ("왼쪽 증상", [1.0, 0.0]),
    ("중심 증상", [1.0, 1.0]),
    ("오른쪽 증상", [0.0, 1.0]),
]
assert summarize_group_symptoms(
    medoid_test_symptoms,
) == "중심 증상", (
    "다른 증상들과 평균 유사도가 가장 높은 문장이 "
    "대표 증상으로 선택되어야 합니다."
)
assert summarize_group_symptoms(
    list(reversed(medoid_test_symptoms)),
) == "중심 증상", (
    "입력 순서가 바뀌어도 대표 증상은 동일해야 합니다."
)

clustered_symptoms = [
    (symptom, embedding_by_id[report_id])
    for report_id, _, symptom in reports
    if report_id in group_members_by_id["r1"]
]
representative_symptom = summarize_group_symptoms(clustered_symptoms)
assert representative_symptom in {
    symptom
    for symptom, _ in clustered_symptoms
}, "대표 증상은 해당 그룹의 실제 증상 문장이어야 합니다."


def canonical_member_sets(cluster_groups):
    """대표 ID와 그룹 출력 순서를 제외하고 멤버 집합만 비교한다."""
    return frozenset(
        frozenset(member.technical_symptom_id for member in group)
        for group in cluster_groups
    )


# A-B와 B-C는 threshold를 통과하지만 A-C는 통과하지 않는 연쇄 구조다.
# 현재 대표 고정 방식이라면 A가 먼저일 때와 B가 먼저일 때 결과가 달라진다.
order_dependency_reports = [
    ("chain_a", "ORDER_SUBMISSION_FAILURE", [1.0, 0.0]),
    ("chain_b", "ORDER_SUBMISSION_FAILURE", [0.8660254, 0.5]),
    ("chain_c", "ORDER_SUBMISSION_FAILURE", [0.5, 0.8660254]),
]
order_dependency_permuted = [
    order_dependency_reports[1],
    order_dependency_reports[0],
    order_dependency_reports[2],
]

original_member_sets = canonical_member_sets(
    group_similar_reports(order_dependency_reports, now=datetime.now(UTC))
)
permuted_member_sets = canonical_member_sets(
    group_similar_reports(order_dependency_permuted, now=datetime.now(UTC))
)

print()
print("입력 순서 불변성 검증:")
print("  A-B-C 순서:", original_member_sets)
print("  B-A-C 순서:", permuted_member_sets)

assert original_member_sets == permuted_member_sets, (
    "동일한 제보 집합의 입력 순서만 바뀌었는데 군집 멤버 구성이 달라졌습니다. "
    "현재 대표 고정 그리디 군집은 입력 순서에 의존합니다."
)

print("군집 및 공통 증상 요약 정책 검증 통과")
