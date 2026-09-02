from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.codes import ObjectDeletionStatus, SignalProcessingStatus
from app.models import IdempotencyRecord, ObjectDeletionJob, SignalProcessingJob
from app.schemas import OperationalMetricsResponse

PROVIDER_FAILURE_WINDOW = timedelta(minutes=15)


async def collect_operational_metrics(
    session: AsyncSession,
    *,
    now: datetime,
) -> OperationalMetricsResponse:
    signal_ready = await session.scalar(
        select(func.count())
        .select_from(SignalProcessingJob)
        .where(
            SignalProcessingJob.status.in_(
                (
                    SignalProcessingStatus.PENDING.value,
                    SignalProcessingStatus.FAILED.value,
                    SignalProcessingStatus.PROCESSING.value,
                )
            ),
            SignalProcessingJob.next_attempt_at <= now,
        )
    )
    signal_repeated_failures = await session.scalar(
        select(func.count())
        .select_from(SignalProcessingJob)
        .where(
            SignalProcessingJob.status == SignalProcessingStatus.FAILED.value,
            SignalProcessingJob.attempt_count >= 2,
        )
    )
    signal_dead_letters = await session.scalar(
        select(func.count())
        .select_from(SignalProcessingJob)
        .where(SignalProcessingJob.status == SignalProcessingStatus.DEAD_LETTER.value)
    )
    provider_failures_recent = await session.scalar(
        select(func.count())
        .select_from(IdempotencyRecord)
        .where(
            IdempotencyRecord.operation == "ANALYZE_REPORT",
            IdempotencyRecord.safe_failure_code.is_not(None),
            IdempotencyRecord.created_at >= now - PROVIDER_FAILURE_WINDOW,
        )
    )
    object_deletion_retries = await session.scalar(
        select(func.count())
        .select_from(ObjectDeletionJob)
        .where(ObjectDeletionJob.status == ObjectDeletionStatus.RETRY_PENDING.value)
    )
    object_deletion_ready = await session.scalar(
        select(func.count())
        .select_from(ObjectDeletionJob)
        .where(
            ObjectDeletionJob.status.in_(
                (
                    ObjectDeletionStatus.PENDING.value,
                    ObjectDeletionStatus.RETRY_PENDING.value,
                    ObjectDeletionStatus.PROCESSING.value,
                )
            ),
            ObjectDeletionJob.next_attempt_at <= now,
        )
    )
    return OperationalMetricsResponse(
        observed_at=now,
        signal_jobs_ready=signal_ready or 0,
        signal_jobs_repeated_failures=signal_repeated_failures or 0,
        signal_jobs_dead_letter=signal_dead_letters or 0,
        provider_failures_last_15m=provider_failures_recent or 0,
        object_deletion_jobs_ready=object_deletion_ready or 0,
        object_deletion_jobs_retrying=object_deletion_retries or 0,
    )


def operational_alerts(metrics: OperationalMetricsResponse) -> tuple[str, ...]:
    alerts: list[str] = []
    if metrics.signal_jobs_repeated_failures:
        alerts.append("SIGNAL_JOB_REPEATED_FAILURE")
    if metrics.signal_jobs_dead_letter:
        alerts.append("SIGNAL_JOB_DEAD_LETTER")
    if metrics.provider_failures_last_15m:
        alerts.append("AI_PROVIDER_FAILURE")
    if metrics.object_deletion_jobs_retrying:
        alerts.append("OBJECT_DELETION_RETRY")
    return tuple(alerts)
