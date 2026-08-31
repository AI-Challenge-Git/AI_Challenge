"""
evaluate_issue_types_dev80.py / evaluate_issue_types_locked80.py가 공유하는
채점 실행부. 로직은 evaluate_issue_types_v4.py의 main()과 동일하다
(정답률, evidence_quote substring 검증, 전용 분류 호출/override 집계,
오류유형별 정답률, confusion matrix, 오분류 상세를 출력한다).

이 파일은 직접 실행하지 않는다 - dev80/locked80 쪽에서 run(CASES, ...)을
호출해서 쓴다.
"""

import hashlib
import json
from collections import Counter, defaultdict

from app.codes import IssueType
from app.real_extractor_v5 import RealDualExtractor
from evaluate_issue_types import normalized_issue_type


def dataset_fingerprint(cases: list[tuple[str, IssueType, str]]) -> str:
    payload = [(case_id, expected.value, text) for case_id, expected, text in cases]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def run(
    cases: list[tuple[str, IssueType, str]],
    dataset_version: str,
    *,
    hard_negative_prefix: str | None = None,
    hard_negative_min_correct: int | None = None,
) -> bool:
    """cases를 RealDualExtractor로 순차로 채점하고 결과를 출력한다. 최종 PASS 여부를 반환한다.

    hard_negative_prefix가 주어지면, case_id가 이 접두사로 시작하는 케이스만 따로
    묶어서 hard_negative_min_correct 이상 맞았는지도 최종 판정에 포함한다.
    """
    extractor = RealDualExtractor()
    confusion: defaultdict[str, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    correct: Counter = Counter()
    failures = []

    extraction_failures = 0
    total_attempts = 0
    classifier_calls_total = 0
    classifier_overrides_total = 0

    evidence_checked = 0
    evidence_substring_ok = 0
    evidence_full_text_count = 0
    full_text_cases: list[str] = []

    hard_negative_total = 0
    hard_negative_correct = 0

    print(f"dataset version: {dataset_version}")
    print(f"dataset fingerprint: {dataset_fingerprint(cases)}")
    print(f"평가 문장 수: {len(cases)}\n")

    for index, (case_id, expected, text) in enumerate(cases, 1):
        outcome = extractor.extract_safe(text)
        expected_value = expected.value
        totals[expected_value] += 1
        total_attempts += outcome.attempt_count
        classifier_calls_total += outcome.classification_call_count
        classifier_overrides_total += int(outcome.classification_override_applied)

        if outcome.result is None:
            predicted = "<EXTRACTION_FAILED>"
            status = "<NO_RESULT>"
            extraction_failures += 1
        else:
            field = outcome.result.technical.issue_type
            predicted = normalized_issue_type(field.value, field.status)
            status = field.status.value

            evidence = field.evidence_quote
            if evidence is not None:
                evidence_checked += 1
                if evidence in text:
                    evidence_substring_ok += 1
                if evidence == text:
                    evidence_full_text_count += 1
                    full_text_cases.append(case_id)

        passed = predicted == expected_value
        confusion[expected_value][predicted] += 1
        if passed:
            correct[expected_value] += 1
        else:
            failures.append((case_id, expected_value, predicted, status, text))

        if hard_negative_prefix is not None and case_id.startswith(hard_negative_prefix):
            hard_negative_total += 1
            if passed:
                hard_negative_correct += 1

        print(
            f"[{index:02d}/{len(cases)}] {'PASS' if passed else 'FAIL'} "
            f"{case_id}: expected={expected_value}, predicted={predicted}, "
            f"status={status}, attempts={outcome.attempt_count}, "
            f"classifier_calls={outcome.classification_call_count}, "
            f"classifier_override={outcome.classification_override_applied}"
        )

    total_correct = sum(correct.values())
    accuracy = total_correct / len(cases)
    avg_attempts = total_attempts / len(cases)
    evidence_substring_rate = evidence_substring_ok / evidence_checked if evidence_checked else 1.0
    full_text_rate = evidence_full_text_count / evidence_checked if evidence_checked else 0.0

    print("\n=== 전체 결과 ===")
    print(f"정답={total_correct}/{len(cases)}")
    print(f"Accuracy={accuracy:.6f}")
    print(f"추출 실패={extraction_failures}")
    print(f"평균 attempt_count={avg_attempts:.6f}")
    print(f"전용 분류 호출 합계={classifier_calls_total}")
    print(f"전용 분류 override 합계={classifier_overrides_total}")

    print("\n=== evidence_quote 품질 ===")
    print(f"evidence_quote 존재 필드 수={evidence_checked}")
    print(f"substring 검증 통과율={evidence_substring_rate:.6f} (목표: 1.0)")
    print(f"원문 전체를 evidence로 사용한 비율={full_text_rate:.6f} (목표: 0.0)")
    if full_text_cases:
        print(f"원문 전체가 evidence로 쓰인 케이스: {full_text_cases}")

    print("\n=== 오류유형별 정답률 ===")
    per_type_pass = True
    for issue_type in IssueType:
        label = issue_type.value
        type_total = totals[label]
        type_correct = correct[label]
        rate = type_correct / type_total if type_total else 0.0
        per_type_pass &= rate >= 0.75
        print(f"{label}: {type_correct}/{type_total} ({rate:.6f})")

    hard_negative_pass = (
        hard_negative_min_correct is None or hard_negative_correct >= hard_negative_min_correct
    )
    if hard_negative_prefix is not None:
        print("\n=== hard negative 세부 결과 ===")
        print(
            f"hard negative 정답={hard_negative_correct}/{hard_negative_total} "
            f"(기준: {hard_negative_min_correct} 이상)"
        )
        print("PASS" if hard_negative_pass else "FAIL")

    base_pass = (
        accuracy >= 0.85
        and extraction_failures == 0
        and per_type_pass
        and avg_attempts <= 1.3
        and evidence_substring_rate == 1.0
        and hard_negative_pass
    )
    strict_pass = base_pass and full_text_rate == 0.0
    relaxed_pass = base_pass

    print("\n=== 최종 합격 판정 (엄격: full_text_rate=0.0 포함) ===")
    print("PASS" if strict_pass else "FAIL")

    print("\n=== 최종 합격 판정 (완화: full_text_rate는 참고 지표만) ===")
    print("PASS" if relaxed_pass else "FAIL")

    overall_pass = relaxed_pass

    print("\n=== Confusion matrix (정답 -> 예측 분포) ===")
    for issue_type in IssueType:
        label = issue_type.value
        print(f"{label} -> {dict(confusion[label])}")

    print("\n=== 오분류 상세 ===")
    if not failures:
        print("없음")
    else:
        for case_id, expected_value, predicted, status, text in failures:
            print(f"{case_id}: expected={expected_value}, predicted={predicted}, status={status}")
            print(f"  입력: {text}")

    return overall_pass


if __name__ == "__main__":
    raise SystemExit(
        "이 파일은 직접 실행하지 않습니다. "
        "evaluate_issue_types_dev80.py 또는 evaluate_issue_types_locked80.py를 실행하세요."
    )
