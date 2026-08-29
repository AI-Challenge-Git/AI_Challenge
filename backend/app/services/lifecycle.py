import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.attachments import LocalAttachmentStore
from app.codes import ObjectDeletionStatus
from app.models import (
    AgentAccessToken,
    Attachment,
    AuditLog,
    IdempotencyRecord,
    ObjectDeletionJob,
    RateLimitBucket,
    Report,
)

CARD_ACCESS_TTL = timedelta(hours=2)
RETENTION_PERIOD = timedelta(hours=72)
DELETION_LEASE = timedelta(minutes=5)
MAX_RETRY_DELAY = timedelta(hours=1)


def utc_now() -> datetime:
    return datetime.now(UTC)


def retention_deadline(received_at: datetime) -> datetime:
    if received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    return received_at + RETENTION_PERIOD


def card_is_accessible(expires_at: datetime | None, *, now: datetime) -> bool:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return expires_at is not None and expires_at.utcoffset() is not None and now < expires_at


@dataclass(frozen=True, slots=True)
class DeletionRunResult:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class PurgePreview:
    reports: int
    idempotency_records: int
    audit_logs: int
    completed_deletion_jobs: int
    attachment_objects: int
    retry_ready_objects: int
    expired_agent_tokens: int
    expired_rate_limit_buckets: int


@dataclass(frozen=True, slots=True)
class PurgeRunResult:
    reports_deleted: int
    idempotency_deleted: int
    audit_logs_deleted: int
    deletion_jobs_deleted: int
    object_deletions_succeeded: int
    object_deletions_failed: int
    object_deletions_skipped: int
    retry_waiting: int
    agent_tokens_deleted: int
    rate_limit_buckets_deleted: int


@dataclass(frozen=True, slots=True)
class _DeletionClaim:
    job_id: UUID
    object_key: str
    attempt_count: int


async def queue_object_deletion(
    session: AsyncSession,
    object_key: str | None,
    *,
    now: datetime,
) -> UUID | None:
    if object_key is None:
        return None
    job_id = uuid4()
    statement = (
        insert(ObjectDeletionJob)
        .values(
            id=job_id,
            object_key=object_key,
            status=ObjectDeletionStatus.PENDING.value,
            attempt_count=0,
            safe_error_code=None,
            next_attempt_at=now,
            completed_at=None,
            purge_at=None,
        )
        .on_conflict_do_nothing(index_elements=[ObjectDeletionJob.object_key])
        .returning(ObjectDeletionJob.id)
    )
    created_id = cast(UUID | None, await session.scalar(statement))
    if created_id is not None:
        return created_id
    return cast(
        UUID | None,
        await session.scalar(
            select(ObjectDeletionJob.id).where(ObjectDeletionJob.object_key == object_key)
        ),
    )


def _eligible_deletion_jobs(now: datetime) -> ColumnElement[bool]:
    return or_(
        (
            (ObjectDeletionJob.status == ObjectDeletionStatus.PENDING.value)
            & (ObjectDeletionJob.next_attempt_at <= now)
        ),
        (
            ObjectDeletionJob.status.in_(
                (
                    ObjectDeletionStatus.PROCESSING.value,
                    ObjectDeletionStatus.RETRY_PENDING.value,
                )
            )
            & (ObjectDeletionJob.next_attempt_at <= now)
        ),
    )


async def _claim_deletion_jobs(
    session: AsyncSession,
    *,
    now: datetime,
    batch_size: int,
    job_ids: tuple[UUID, ...] | None,
) -> list[_DeletionClaim]:
    async with session.begin():
        statement = (
            select(ObjectDeletionJob)
            .where(_eligible_deletion_jobs(now))
            .order_by(ObjectDeletionJob.next_attempt_at, ObjectDeletionJob.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        if job_ids is not None:
            statement = statement.where(ObjectDeletionJob.id.in_(job_ids))
        jobs = list((await session.scalars(statement)).all())
        claims: list[_DeletionClaim] = []
        for job in jobs:
            job.status = ObjectDeletionStatus.PROCESSING.value
            job.attempt_count += 1
            job.safe_error_code = None
            job.next_attempt_at = now + DELETION_LEASE
            job.updated_at = now
            claims.append(_DeletionClaim(job.id, job.object_key, job.attempt_count))
        return claims


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(60 * (2 ** min(attempt_count - 1, 6)), int(MAX_RETRY_DELAY.total_seconds()))
    return timedelta(seconds=seconds)


async def process_object_deletion_jobs(
    session: AsyncSession,
    attachment_store: LocalAttachmentStore,
    *,
    now: datetime | None = None,
    batch_size: int = 100,
    job_ids: tuple[UUID, ...] | None = None,
) -> DeletionRunResult:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    current_time = now or utc_now()
    claims = await _claim_deletion_jobs(
        session,
        now=current_time,
        batch_size=batch_size,
        job_ids=job_ids,
    )
    succeeded = 0
    failed = 0
    skipped = 0
    for claim in claims:
        storage_failed = False
        try:
            await attachment_store.delete(claim.object_key)
        except Exception:
            storage_failed = True

        outcome_time = now or utc_now()
        async with session.begin():
            job = await session.scalar(
                select(ObjectDeletionJob)
                .where(ObjectDeletionJob.id == claim.job_id)
                .with_for_update()
            )
            if (
                job is None
                or job.status != ObjectDeletionStatus.PROCESSING.value
                or job.attempt_count != claim.attempt_count
            ):
                skipped += 1
                continue
            job.updated_at = outcome_time
            if storage_failed:
                job.status = ObjectDeletionStatus.RETRY_PENDING.value
                job.safe_error_code = "STORAGE_UNAVAILABLE"
                job.next_attempt_at = outcome_time + _retry_delay(job.attempt_count)
                failed += 1
            else:
                job.status = ObjectDeletionStatus.COMPLETED.value
                job.safe_error_code = None
                job.next_attempt_at = None
                job.completed_at = outcome_time
                job.purge_at = retention_deadline(outcome_time)
                succeeded += 1
    return DeletionRunResult(succeeded=succeeded, failed=failed, skipped=skipped)


async def preview_purge(session: AsyncSession, *, now: datetime) -> PurgePreview:
    audit_cutoff = now - RETENTION_PERIOD
    reports = await session.scalar(
        select(func.count()).select_from(Report).where(Report.purge_at <= now)
    )
    attachment_objects = await session.scalar(
        select(func.count()).select_from(Attachment).join(Report).where(Report.purge_at <= now)
    )
    idempotency_records = await session.scalar(
        select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.purge_at <= now)
    )
    audit_logs = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.created_at <= audit_cutoff)
    )
    completed_deletion_jobs = await session.scalar(
        select(func.count())
        .select_from(ObjectDeletionJob)
        .where(
            ObjectDeletionJob.status == ObjectDeletionStatus.COMPLETED.value,
            ObjectDeletionJob.purge_at <= now,
        )
    )
    retry_ready_objects = await session.scalar(
        select(func.count()).select_from(ObjectDeletionJob).where(_eligible_deletion_jobs(now))
    )
    expired_agent_tokens = await session.scalar(
        select(func.count()).select_from(AgentAccessToken).where(AgentAccessToken.expires_at <= now)
    )
    expired_rate_limit_buckets = await session.scalar(
        select(func.count()).select_from(RateLimitBucket).where(RateLimitBucket.expires_at <= now)
    )
    return PurgePreview(
        reports=reports or 0,
        idempotency_records=idempotency_records or 0,
        audit_logs=audit_logs or 0,
        completed_deletion_jobs=completed_deletion_jobs or 0,
        attachment_objects=attachment_objects or 0,
        retry_ready_objects=retry_ready_objects or 0,
        expired_agent_tokens=expired_agent_tokens or 0,
        expired_rate_limit_buckets=expired_rate_limit_buckets or 0,
    )


async def _purge_expired_reports(
    session: AsyncSession,
    *,
    now: datetime,
    batch_size: int,
) -> int:
    total = 0
    while True:
        async with session.begin():
            reports = list(
                (
                    await session.scalars(
                        select(Report)
                        .where(Report.purge_at <= now)
                        .options(selectinload(Report.attachment))
                        .order_by(Report.purge_at, Report.id)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for report in reports:
                if report.attachment is not None:
                    await queue_object_deletion(session, report.attachment.object_key, now=now)
                session.add(
                    AuditLog(
                        actor_type="retention_worker",
                        action="REPORT_RETENTION_PURGED",
                        resource_fingerprint=hashlib.sha256(report.id.bytes).hexdigest(),
                        created_at=now,
                    )
                )
                await session.execute(delete(Report).where(Report.id == report.id))
            count = len(reports)
            total += count
        if count < batch_size:
            return total


async def _purge_rows(
    session: AsyncSession,
    model: type[Any],
    predicate: ColumnElement[bool],
    order_column: Any,
    *,
    batch_size: int,
) -> int:
    total = 0
    while True:
        async with session.begin():
            rows = list(
                (
                    await session.scalars(
                        select(model)
                        .where(predicate)
                        .order_by(order_column, model.id)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                await session.delete(row)
            count = len(rows)
            total += count
        if count < batch_size:
            return total


async def purge_expired_data(
    session: AsyncSession,
    attachment_store: LocalAttachmentStore,
    *,
    now: datetime | None = None,
    batch_size: int = 100,
) -> PurgeRunResult:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    current_time = now or utc_now()
    reports_deleted = await _purge_expired_reports(session, now=current_time, batch_size=batch_size)
    idempotency_deleted = await _purge_rows(
        session,
        IdempotencyRecord,
        IdempotencyRecord.purge_at <= current_time,
        IdempotencyRecord.purge_at,
        batch_size=batch_size,
    )
    audit_logs_deleted = await _purge_rows(
        session,
        AuditLog,
        AuditLog.created_at <= current_time - RETENTION_PERIOD,
        AuditLog.created_at,
        batch_size=batch_size,
    )
    deletion_jobs_deleted = await _purge_rows(
        session,
        ObjectDeletionJob,
        (ObjectDeletionJob.status == ObjectDeletionStatus.COMPLETED.value)
        & (ObjectDeletionJob.purge_at <= current_time),
        ObjectDeletionJob.purge_at,
        batch_size=batch_size,
    )
    agent_tokens_deleted = await _purge_rows(
        session,
        AgentAccessToken,
        AgentAccessToken.expires_at <= current_time,
        AgentAccessToken.expires_at,
        batch_size=batch_size,
    )
    rate_limit_buckets_deleted = await _purge_rows(
        session,
        RateLimitBucket,
        RateLimitBucket.expires_at <= current_time,
        RateLimitBucket.expires_at,
        batch_size=batch_size,
    )
    object_result = await process_object_deletion_jobs(
        session,
        attachment_store,
        now=current_time,
        batch_size=batch_size,
    )
    async with session.begin():
        retry_waiting = await session.scalar(
            select(func.count())
            .select_from(ObjectDeletionJob)
            .where(ObjectDeletionJob.status != ObjectDeletionStatus.COMPLETED.value)
        )
    return PurgeRunResult(
        reports_deleted=reports_deleted,
        idempotency_deleted=idempotency_deleted,
        audit_logs_deleted=audit_logs_deleted,
        deletion_jobs_deleted=deletion_jobs_deleted,
        object_deletions_succeeded=object_result.succeeded,
        object_deletions_failed=object_result.failed,
        object_deletions_skipped=object_result.skipped,
        retry_waiting=retry_waiting or 0,
        agent_tokens_deleted=agent_tokens_deleted,
        rate_limit_buckets_deleted=rate_limit_buckets_deleted,
    )
