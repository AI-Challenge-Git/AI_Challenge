"""
AI-05, AI-11, FE-07 규칙이 실제로 코드에서 강제되는지 확인하는 수동 테스트.
backend 폴더 안에서 실행: python test_manual.py
"""

from typing import Any


def test_1_issuetype_import() -> None:
    from app.codes import IssueType

    values = list(IssueType)
    assert len(values) == 7, f"IssueType 값 개수가 7이 아님: {len(values)}"
    print("PASS [1] IssueType import 및 값 개수 확인:", values)


async def test_2_fake_extractor() -> None:
    from app.ai import FakeDualExtractor

    e = FakeDualExtractor()
    result = await e.extract("테스트 텍스트 20자 이상 채우기용 문장입니다")
    print("PASS [2] FakeDualExtractor 정상 동작")
    print(result.model_dump_json(indent=2))


def test_3_out_of_scope_rejects_value() -> None:
    from app.codes import FieldStatus
    from app.schemas import CandidateField

    try:
        CandidateField(
            value="삼성전자",
            status=FieldStatus.OUT_OF_SCOPE,
            evidence_quote="삼성전자",
        )
        print("FAIL [3] OUT_OF_SCOPE인데 value가 있어도 에러가 안 남 (AI-05 위반)")
    except Exception as e:
        print("PASS [3] AI-05:", e)


def test_4_placeholder_cannot_resolve_to_value() -> None:
    from app.ai import validate_no_restored_pii
    from app.codes import FieldStatus
    from app.schemas import (
        CandidateField,
        ConsultationCandidate,
        ExtractionResult,
        TechnicalCandidate,
    )

    def unk() -> CandidateField[Any]:
        return CandidateField(value=None, status=FieldStatus.UNKNOWN, evidence_quote=None)

    bad = ExtractionResult(
        schema_version="v1",
        taxonomy_version="v1",
        adapter_name="test",
        model_id=None,
        technical=TechnicalCandidate(
            issue_type=unk(),
            symptom=unk(),
            submission_status=unk(),
            error_code=unk(),
            reported_occurred_at=unk(),
        ),
        consultation=ConsultationCandidate(
            action=unk(),
            symbol_name=CandidateField(
                value="010-1234-5678",
                status=FieldStatus.CONFIRMED_FROM_TEXT,
                evidence_quote="[PHONE]",
            ),
            symbol_code=unk(),
            quantity=unk(),
            order_type=unk(),
            price_krw=unk(),
            attempted_at=unk(),
        ),
    )

    try:
        validate_no_restored_pii(bad)
        print("FAIL [4] placeholder를 실제 값으로 복원했는데 에러가 안 남 (AI-11 위반)")
    except Exception as e:
        print("PASS [4] AI-11:", e)


def test_5_dateless_time_cannot_be_confirmed() -> None:
    from app.codes import FieldStatus
    from app.schemas import CandidateField, TechnicalCandidate

    def unk() -> CandidateField[Any]:
        return CandidateField(value=None, status=FieldStatus.UNKNOWN, evidence_quote=None)

    try:
        TechnicalCandidate(
            issue_type=unk(),
            symptom=unk(),
            submission_status=unk(),
            error_code=unk(),
            reported_occurred_at=CandidateField(
                value="09:03",
                status=FieldStatus.CONFIRMED_FROM_TEXT,
                evidence_quote="09:03",
            ),
        )
        print("FAIL [5] 날짜 없는 시각인데 CONFIRMED_FROM_TEXT가 허용됨 (FE-07 위반)")
    except Exception as e:
        print("PASS [5] FE-07:", e)


if __name__ == "__main__":
    import asyncio

    print("=" * 60)
    test_1_issuetype_import()
    print("=" * 60)
    asyncio.run(test_2_fake_extractor())
    print("=" * 60)
    test_3_out_of_scope_rejects_value()
    print("=" * 60)
    test_4_placeholder_cannot_resolve_to_value()
    print("=" * 60)
    test_5_dateless_time_cannot_be_confirmed()
    print("=" * 60)
    print("전체 테스트 완료. 위에서 FAIL이 하나도 없어야 정상입니다.")
