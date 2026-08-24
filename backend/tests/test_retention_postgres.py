import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.attachments import AttachmentStorageError, LocalAttachmentStore
from app.codes import AnalysisStatus, ReportStatus
from app.db import engine, session_factory
from app.models import (
    Attachment,
    AuditLog,
    ConsultationCard,
    IdempotencyRecord,
    ObjectDeletionJob,
    PolicySnapshot,
    Report,
    ReportAnalysis,
    TechnicalSymptom,
)
from app.services.lifecycle import (
    RETENTION_PERIOD,
    preview_purge,
    process_object_deletion_jobs,
    purge_expired_data,
    queue_object_deletion,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires migrated PostgreSQL",
)


class DeleteFailOnceStore(LocalAttachmentStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failures_remaining = 1

    async def delete(self, object_key: str) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise AttachmentStorageError("synthetic internal storage error")
        await super().delete(object_key)


async def _clean() -> None:
    async with session_factory() as session, session.begin():
        await session.execute(delete(Report))
        await session.execute(delete(IdempotencyRecord))
        await session.execute(delete(AuditLog))
        await session.execute(delete(ObjectDeletionJob))


@pytest.fixture(autouse=True)
async def clean_business_data() -> AsyncIterator[None]:
    await _clean()
    yield
    await _clean()
    await engine.dispose()


async def _seed_report(
    *,
    received_at: datetime,
    full_children: bool = False,
    object_key: str | None = None,
) -> Report:
    async with session_factory() as session, session.begin():
        policy = await session.scalar(select(PolicySnapshot).limit(1))
        assert policy is not None
        report = Report(
            session_digest=secrets.token_bytes(32),
            client_request_id=uuid4(),
            policy_snapshot_id=policy.id,
            pii_policy_version="pii-mask.v1",
            masked_text="보존기간 테스트용 비식별 합성 제보입니다.",
            request_payload_sha256=secrets.token_hex(32),
            status=(
                ReportStatus.CONFIRMED.value
                if full_children
                else ReportStatus.ANALYSIS_PENDING.value
            ),
            received_at=received_at,
            purge_at=received_at + RETENTION_PERIOD,
            confirmed_at=received_at if full_children else None,
            updated_at=received_at,
        )
        analysis = ReportAnalysis(
            version=1,
            schema_version="test.schema.v1",
            taxonomy_version="test.taxonomy.v1",
            adapter_name="fake",
            model_id=None,
            status=(
                AnalysisStatus.SUCCEEDED.value if full_children else AnalysisStatus.PENDING.value
            ),
        )
        if full_children:
            analysis.technical_candidate = {}
            analysis.consultation_candidate = {}
            analysis.completed_at = received_at
        report.analyses.append(analysis)
        if full_children:
            report.technical_symptom = TechnicalSymptom(
                taxonomy_version="test.taxonomy.v1",
                channel="MABLE",
                feature_area="DOMESTIC_STOCK_ORDER",
                issue_type="UNKNOWN",
                symptom=None,
                submission_status="UNKNOWN",
                error_code=None,
                reported_occurred_at=None,
                confirmed_at=received_at,
            )
            report.consultation_card = ConsultationCard(
                action="UNKNOWN",
                symbol_name=None,
                symbol_code=None,
                quantity=None,
                order_type="UNKNOWN",
                price_krw=None,
                attempted_at=None,
            )
        if object_key is not None:
            report.attachment = Attachment(
                object_key=object_key,
                content_type="image/png",
                byte_size=8,
                width=1,
                height=1,
                content_sha256="a" * 64,
            )
        session.add(report)
        await session.flush()
        return report


async def test_object_deletion_is_retryable_idempotent_and_treats_missing_as_success(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 24, 6, 0, tzinfo=UTC)
    store = DeleteFailOnceStore(tmp_path / "objects")
    object_key = secrets.token_urlsafe(32)
    await LocalAttachmentStore.put(store, object_key, b"synthetic")

    async with session_factory() as session:
        async with session.begin():
            job_id = await queue_object_deletion(session, object_key, now=now)
        assert job_id is not None
        failed = await process_object_deletion_jobs(
            session, store, now=now, batch_size=1, job_ids=(job_id,)
        )
        assert failed.failed == 1
        job = await session.get(ObjectDeletionJob, job_id)
        assert job is not None and job.status == "RETRY_PENDING"
        assert job.next_attempt_at is not None
        retry_at = job.next_attempt_at
        await session.rollback()

        succeeded = await process_object_deletion_jobs(
            session,
            store,
            now=retry_at,
            batch_size=1,
            job_ids=(job_id,),
        )
        assert succeeded.succeeded == 1
        assert not (store.root / object_key).exists()

        repeated = await process_object_deletion_jobs(
            session,
            store,
            now=now + RETENTION_PERIOD,
            batch_size=1,
            job_ids=(job_id,),
        )
        assert repeated.succeeded == repeated.failed == 0

        missing_key = secrets.token_urlsafe(32)
        async with session.begin():
            missing_job_id = await queue_object_deletion(session, missing_key, now=now)
        assert missing_job_id is not None
        missing = await process_object_deletion_jobs(
            session,
            store,
            now=now,
            batch_size=1,
            job_ids=(missing_job_id,),
        )
        assert missing.succeeded == 1


async def test_purge_dry_run_execute_repeat_and_report_root_cascade(tmp_path: Path) -> None:
    now = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
    store = LocalAttachmentStore(tmp_path / "purge-objects")
    object_key = secrets.token_urlsafe(32)
    await store.put(object_key, b"synthetic")
    await _seed_report(
        received_at=now - RETENTION_PERIOD - timedelta(microseconds=1),
        full_children=True,
        object_key=object_key,
    )
    await _seed_report(received_at=now - RETENTION_PERIOD)
    await _seed_report(received_at=now - RETENTION_PERIOD + timedelta(microseconds=1))

    async with session_factory() as session, session.begin():
        session.add_all(
            (
                IdempotencyRecord(
                    principal_digest=secrets.token_bytes(32),
                    operation="TEST_OLD",
                    client_request_id=uuid4(),
                    payload_sha256=secrets.token_hex(32),
                    response_status=204,
                    safe_failure_code=None,
                    processing_status="COMPLETED",
                    created_at=now - RETENTION_PERIOD,
                    completed_at=now - RETENTION_PERIOD,
                    purge_at=now,
                ),
                IdempotencyRecord(
                    principal_digest=secrets.token_bytes(32),
                    operation="TEST_NEW",
                    client_request_id=uuid4(),
                    payload_sha256=secrets.token_hex(32),
                    response_status=204,
                    safe_failure_code=None,
                    processing_status="COMPLETED",
                    created_at=now,
                    completed_at=now,
                    purge_at=now + RETENTION_PERIOD,
                ),
                AuditLog(
                    actor_type="test",
                    action="OLD_SAFE_EVENT",
                    resource_fingerprint=secrets.token_hex(32),
                    created_at=now - RETENTION_PERIOD,
                ),
                AuditLog(
                    actor_type="test",
                    action="NEW_SAFE_EVENT",
                    resource_fingerprint=secrets.token_hex(32),
                    created_at=now,
                ),
            )
        )

    async with session_factory() as session:
        before = await preview_purge(session, now=now)
        assert before.reports == 2
        assert before.attachment_objects == 1
        assert before.idempotency_records == 1
        assert before.audit_logs == 1
        assert await session.scalar(select(func.count()).select_from(Report)) == 3
        await session.rollback()

        result = await purge_expired_data(session, store, now=now, batch_size=1)
        assert result.reports_deleted == 2
        assert result.idempotency_deleted == 1
        assert result.audit_logs_deleted == 1
        assert result.object_deletions_succeeded == 1
        assert result.retry_waiting == 0
        assert not (store.root / object_key).exists()

        assert await session.scalar(select(func.count()).select_from(Report)) == 1
        assert await session.scalar(select(func.count()).select_from(ReportAnalysis)) == 1
        assert await session.scalar(select(func.count()).select_from(TechnicalSymptom)) == 0
        assert await session.scalar(select(func.count()).select_from(ConsultationCard)) == 0
        assert await session.scalar(select(func.count()).select_from(Attachment)) == 0
        await session.rollback()

        repeated = await purge_expired_data(session, store, now=now, batch_size=1)
        assert repeated.reports_deleted == 0
        assert repeated.idempotency_deleted == 0
        assert repeated.object_deletions_succeeded == 0


async def test_two_purge_workers_do_not_delete_the_same_report_twice(tmp_path: Path) -> None:
    now = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    for offset in range(6):
        await _seed_report(received_at=now - RETENTION_PERIOD - timedelta(seconds=offset))

    async def worker() -> int:
        async with session_factory() as session:
            result = await purge_expired_data(
                session,
                LocalAttachmentStore(tmp_path / "concurrent-objects"),
                now=now,
                batch_size=1,
            )
            return result.reports_deleted

    counts = await asyncio.gather(worker(), worker())
    assert sum(counts) == 6
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
