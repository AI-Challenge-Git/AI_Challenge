from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Float, cast, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.codes import (
    BaselineStatus,
    ClusteringPolicyStatus,
    IssueType,
    RateLimitScope,
    SignalClosureReason,
    SignalProcessingStatus,
    SignalStatus,
    SubmissionStatus,
)
from app.config import Settings
from app.errors import ServiceError
from app.models import (
    ClusteringPolicy,
    IdempotencyRecord,
    Report,
    SignalAuditEvent,
    SignalCluster,
    SignalMember,
    SignalProcessingJob,
    TechnicalEmbedding,
    TechnicalSymptom,
    Vector1024,
)
from app.schemas import (
    OperatorAcknowledgeSignalRequest,
    OperatorCloseSignalRequest,
    OperatorMergeSignalsRequest,
    OperatorOfficialNoticeRequest,
    OperatorSignalMutationResponse,
    OperatorSplitSignalRequest,
    RelatedSignal,
    SignalDashboardItem,
    SignalDashboardResponse,
    SignalEmbeddingRequest,
    SignalEmbeddingResult,
    SignalHourlyVolume,
    SignalPolicySnapshot,
    SignalProcessingResult,
)
from app.security import keyed_fingerprint
from app.services.idempotency import (
    completed_idempotency_record,
    lock_idempotency_key,
    payload_sha256,
)
from app.services.lifecycle import RETENTION_PERIOD
from app.services.rate_limits import consume_rate_limit, rate_limit_error

if TYPE_CHECKING:
    from app.services.agents import AgentPrincipal

SAFE_PROCESSING_ERRORS = {
    "EMBEDDING_UNAVAILABLE",
    "INVALID_EMBEDDING",
    "POLICY_MISMATCH",
    "EMBEDDING_INPUT_UNAVAILABLE",
}
ACTIVE_SIGNAL_STATUSES = (
    SignalStatus.SIGNAL_DETECTED.value,
    SignalStatus.UNDER_REVIEW.value,
)
COSINE_COMPARISON_TOLERANCE = 1e-6
PublicSignalStatus = Literal[SignalStatus.SIGNAL_DETECTED, SignalStatus.UNDER_REVIEW]


class EmbeddingProvider(Protocol):
    async def embed(self, request: SignalEmbeddingRequest) -> SignalEmbeddingResult: ...


Clock = Callable[[], datetime]
EmbeddingCall = Callable[[SignalEmbeddingRequest], Awaitable[SignalEmbeddingResult]]


def _known_submission_filter(value: str) -> str | None:
    return None if value == SubmissionStatus.UNKNOWN.value else value


def _public_signal_status(value: str) -> PublicSignalStatus:
    status = SignalStatus(value)
    if status not in (SignalStatus.SIGNAL_DETECTED, SignalStatus.UNDER_REVIEW):
        raise ValueError("signal is not externally visible")
    return status


def _gate_lock_key(policy_id: UUID, symptom: TechnicalSymptom) -> int:
    digest = hashlib.sha256(
        b"signal-gate:"
        + policy_id.bytes
        + symptom.channel.encode()
        + b":"
        + symptom.feature_area.encode()
        + b":"
        + symptom.issue_type.encode()
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _operator_digest(agent_id: UUID) -> bytes:
    return hashlib.sha256(b"signal-operator:" + agent_id.bytes).digest()


def _audit_event(
    *,
    signal_id: UUID | None,
    action: str,
    now: datetime,
    before_status: str | None,
    after_status: str | None,
    actor_id: UUID | None = None,
    actor_type: str = "signal_worker",
    reason: str | None = None,
    target_signal_id: UUID | None = None,
) -> SignalAuditEvent:
    return SignalAuditEvent(
        signal_id=signal_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        before_status=before_status,
        after_status=after_status,
        reason=reason,
        target_signal_id=target_signal_id,
        created_at=now,
        purge_at=now + RETENTION_PERIOD,
    )


def enqueue_signal_processing(
    *,
    report_id: UUID,
    technical_symptom_id: UUID,
    now: datetime,
) -> SignalProcessingJob:
    return SignalProcessingJob(
        report_id=report_id,
        technical_symptom_id=technical_symptom_id,
        status=SignalProcessingStatus.PENDING.value,
        attempt_count=0,
        next_attempt_at=now,
    )


async def _mark_job_failed(
    session: AsyncSession,
    job_id: UUID,
    safe_error_code: str,
    *,
    now: datetime,
) -> SignalProcessingResult:
    if safe_error_code not in SAFE_PROCESSING_ERRORS:
        raise ValueError("unsafe signal processing error code")
    async with session.begin():
        job = await session.scalar(
            select(SignalProcessingJob).where(SignalProcessingJob.id == job_id).with_for_update()
        )
        if job is None:
            return SignalProcessingResult(
                job_id=job_id,
                status=SignalProcessingStatus.FAILED,
                signal_id=None,
                safe_error_code=safe_error_code,
            )
        job.status = SignalProcessingStatus.FAILED.value
        job.safe_error_code = safe_error_code
        job.next_attempt_at = now + timedelta(minutes=5)
        job.completed_at = now
    return SignalProcessingResult(
        job_id=job_id,
        status=SignalProcessingStatus.FAILED,
        signal_id=None,
        safe_error_code=safe_error_code,
    )


def _validate_embedding_metadata(
    result: SignalEmbeddingResult,
    policy: ClusteringPolicy,
) -> bool:
    return (
        result.model_id == policy.model_id
        and result.model_revision == policy.model_revision
        and result.dimension == policy.embedding_dimension
        and result.normalization == policy.normalization
        and result.input_format == policy.input_format
        and result.distance_metric == policy.distance_metric
        and len(result.vector) == policy.embedding_dimension
        and all(math.isfinite(value) for value in result.vector)
    )


async def _recalculate_cluster(
    session: AsyncSession,
    cluster: SignalCluster,
    *,
    now: datetime,
    deletion_recalculation: bool,
) -> None:
    counts = (
        await session.execute(
            select(
                func.count(SignalMember.id),
                func.count(func.distinct(Report.session_digest)),
                func.min(Report.received_at),
                func.max(Report.received_at),
                func.max(Report.purge_at),
            )
            .select_from(SignalMember)
            .join(Report, Report.id == SignalMember.report_id)
            .where(SignalMember.signal_id == cluster.id)
        )
    ).one()
    raw_count = int(counts[0] or 0)
    unique_sessions = int(counts[1] or 0)
    policy = await session.get(ClusteringPolicy, cluster.policy_id)
    if policy is None:
        raise RuntimeError("signal policy is missing")

    before = cluster.status
    cluster.raw_report_count = raw_count
    cluster.reporting_unique_sessions = unique_sessions
    cluster.review_priority = unique_sessions >= policy.review_priority_threshold
    cluster.representative_symptom_id = None
    if counts[2] is not None:
        cluster.first_report_at = counts[2]
        cluster.last_report_at = counts[3]
        cluster.purge_at = counts[4]

    if unique_sessions < policy.min_unique_sessions and deletion_recalculation:
        cluster.status = SignalStatus.CLOSED.value
        cluster.closure_reason = SignalClosureReason.EVIDENCE_RECALCULATED.value
        cluster.closed_at = now
    elif cluster.status == SignalStatus.CANDIDATE.value and (
        unique_sessions >= policy.min_unique_sessions
    ):
        cluster.status = SignalStatus.SIGNAL_DETECTED.value

    if cluster.status != before:
        session.add(
            _audit_event(
                signal_id=cluster.id,
                action="SIGNAL_STATE_CHANGED",
                now=now,
                before_status=before,
                after_status=cluster.status,
                reason=cluster.closure_reason,
            )
        )


async def detach_report_from_signals(
    session: AsyncSession,
    report_id: UUID,
    *,
    now: datetime,
) -> None:
    signal_ids = list(
        (
            await session.scalars(
                select(SignalMember.signal_id)
                .where(SignalMember.report_id == report_id)
                .with_for_update()
            )
        ).all()
    )
    if not signal_ids:
        return
    clusters = list(
        (
            await session.scalars(
                select(SignalCluster)
                .where(SignalCluster.id.in_(signal_ids))
                .order_by(SignalCluster.id)
                .with_for_update()
            )
        ).all()
    )
    await session.execute(delete(SignalMember).where(SignalMember.report_id == report_id))
    await session.flush()
    for cluster in clusters:
        await _recalculate_cluster(
            session,
            cluster,
            now=now,
            deletion_recalculation=True,
        )


async def process_next_signal_job(
    session: AsyncSession,
    provider: EmbeddingProvider,
    *,
    now: datetime,
) -> SignalProcessingResult | None:
    async with session.begin():
        policy = await session.scalar(
            select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
        )
        if policy is None:
            return None
        job = await session.scalar(
            select(SignalProcessingJob)
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
            .order_by(SignalProcessingJob.next_attempt_at, SignalProcessingJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        symptom = await session.get(TechnicalSymptom, job.technical_symptom_id)
        if symptom is None or symptom.symptom is None:
            job_id = job.id
            job.status = SignalProcessingStatus.PROCESSING.value
            job.policy_id = policy.id
            job.attempt_count += 1
            job.next_attempt_at = now + timedelta(minutes=5)
            missing_input = True
            request = None
        else:
            missing_input = False
            job_id = job.id
            job.status = SignalProcessingStatus.PROCESSING.value
            job.policy_id = policy.id
            job.safe_error_code = None
            job.completed_at = None
            job.attempt_count += 1
            job.next_attempt_at = now + timedelta(minutes=5)
            request = SignalEmbeddingRequest(
                schema_version="signal-embedding-request.v1",
                input_format=policy.input_format,
                technical_symptom=symptom.symptom,
            )
        policy_id = policy.id

    if missing_input or request is None:
        return await _mark_job_failed(
            session,
            job_id,
            "EMBEDDING_INPUT_UNAVAILABLE",
            now=now,
        )
    try:
        raw_result = await provider.embed(request)
        result = SignalEmbeddingResult.model_validate(raw_result)
    except ValidationError:
        return await _mark_job_failed(
            session,
            job_id,
            "INVALID_EMBEDDING",
            now=now,
        )
    except Exception:  # provider exceptions are never exposed outside this boundary
        return await _mark_job_failed(
            session,
            job_id,
            "EMBEDDING_UNAVAILABLE",
            now=now,
        )

    async with session.begin():
        policy = await session.get(ClusteringPolicy, policy_id)
        job = await session.scalar(
            select(SignalProcessingJob).where(SignalProcessingJob.id == job_id).with_for_update()
        )
        if policy is None or job is None:
            raise RuntimeError("signal processing state disappeared")
        if not _validate_embedding_metadata(result, policy):
            job.status = SignalProcessingStatus.FAILED.value
            job.safe_error_code = "POLICY_MISMATCH"
            job.next_attempt_at = now + timedelta(minutes=5)
            job.completed_at = now
            return SignalProcessingResult(
                job_id=job.id,
                status=SignalProcessingStatus.FAILED,
                signal_id=None,
                safe_error_code="POLICY_MISMATCH",
            )
        symptom = await session.get(TechnicalSymptom, job.technical_symptom_id)
        report = await session.get(Report, job.report_id)
        if symptom is None or report is None:
            return None
        if symptom.taxonomy_version != policy.taxonomy_version:
            job.status = SignalProcessingStatus.FAILED.value
            job.safe_error_code = "POLICY_MISMATCH"
            job.next_attempt_at = now + timedelta(minutes=5)
            job.completed_at = now
            return SignalProcessingResult(
                job_id=job.id,
                status=SignalProcessingStatus.FAILED,
                signal_id=None,
                safe_error_code="POLICY_MISMATCH",
            )

        embedding = TechnicalEmbedding(
            technical_symptom_id=symptom.id,
            model_id=result.model_id,
            model_revision=result.model_revision,
            embedding_dimension=result.dimension,
            normalization=result.normalization,
            input_format=result.input_format,
            distance_metric=result.distance_metric,
            embedding=result.vector,
            created_at=now,
        )
        session.add(embedding)
        await session.flush()
        await session.execute(
            select(func.pg_advisory_xact_lock(_gate_lock_key(policy.id, symptom)))
        )

        known_submission = _known_submission_filter(symptom.submission_status)
        embedding_expression = (
            cast(TechnicalEmbedding.embedding, Vector1024())
            if policy.embedding_dimension == 1024
            else TechnicalEmbedding.embedding
        )
        distance = embedding_expression.op("<=>", return_type=Float())(result.vector)
        candidate_filters = [
            SignalCluster.policy_id == policy.id,
            SignalCluster.status != SignalStatus.CLOSED.value,
            SignalCluster.channel == symptom.channel,
            SignalCluster.feature_area == symptom.feature_area,
            SignalCluster.reported_symptom_type == symptom.issue_type,
            SignalCluster.last_report_at
            >= report.received_at - timedelta(seconds=policy.window_seconds),
            TechnicalEmbedding.model_id == policy.model_id,
            TechnicalEmbedding.model_revision == policy.model_revision,
            TechnicalEmbedding.embedding_dimension == policy.embedding_dimension,
            TechnicalEmbedding.normalization == policy.normalization,
            TechnicalEmbedding.input_format == policy.input_format,
            TechnicalEmbedding.distance_metric == policy.distance_metric,
        ]
        if known_submission is not None:
            candidate_filters.append(
                (SignalCluster.submission_status_filter.is_(None))
                | (SignalCluster.submission_status_filter == known_submission)
            )
        if symptom.error_code is not None:
            candidate_filters.append(
                (SignalCluster.error_code_filter.is_(None))
                | (SignalCluster.error_code_filter == symptom.error_code)
            )
        if policy.embedding_dimension == 1024:
            await session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        candidate_row = (
            await session.execute(
                select(SignalCluster, (1.0 - distance).label("similarity"))
                .join(SignalMember, SignalMember.signal_id == SignalCluster.id)
                .join(TechnicalEmbedding, TechnicalEmbedding.id == SignalMember.embedding_id)
                .where(*candidate_filters)
                .order_by(distance, SignalCluster.id, SignalMember.id)
                .limit(1)
            )
        ).first()
        selected = (
            (candidate_row[0], float(candidate_row[1]))
            if candidate_row is not None
            and float(candidate_row[1]) >= policy.similarity_threshold - COSINE_COMPARISON_TOLERANCE
            else None
        )
        if selected is None:
            cluster = SignalCluster(
                policy_id=policy.id,
                status=SignalStatus.CANDIDATE.value,
                channel=symptom.channel,
                feature_area=symptom.feature_area,
                reported_symptom_type=symptom.issue_type,
                submission_status_filter=known_submission,
                error_code_filter=symptom.error_code,
                raw_report_count=0,
                reporting_unique_sessions=0,
                review_priority=False,
                first_report_at=report.received_at,
                last_report_at=report.received_at,
                representative_symptom_id=None,
                official_incident=False,
                created_at=now,
                updated_at=now,
                purge_at=report.purge_at,
            )
            session.add(cluster)
            await session.flush()
            similarity = 1.0
            session.add(
                _audit_event(
                    signal_id=cluster.id,
                    action="SIGNAL_CANDIDATE_CREATED",
                    now=now,
                    before_status=None,
                    after_status=cluster.status,
                )
            )
        else:
            cluster, similarity = selected
            if cluster.submission_status_filter is None:
                cluster.submission_status_filter = known_submission
            if cluster.error_code_filter is None:
                cluster.error_code_filter = symptom.error_code

        session.add(
            SignalMember(
                signal_id=cluster.id,
                report_id=report.id,
                technical_symptom_id=symptom.id,
                embedding_id=embedding.id,
                similarity_at_join=similarity,
                created_at=now,
            )
        )
        await session.flush()
        await _recalculate_cluster(
            session,
            cluster,
            now=now,
            deletion_recalculation=False,
        )
        job.status = SignalProcessingStatus.COMPLETED.value
        job.safe_error_code = None
        job.completed_at = now
        job.next_attempt_at = now
        return SignalProcessingResult(
            job_id=job.id,
            status=SignalProcessingStatus.COMPLETED,
            signal_id=cluster.id,
            safe_error_code=None,
        )


async def list_dashboard_signals(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
    offset: int,
) -> SignalDashboardResponse:
    within_detection_window = (
        SignalCluster.last_report_at + ClusteringPolicy.window_seconds * text("INTERVAL '1 second'")
        >= now
    )
    visible_signal = or_(
        SignalCluster.status == SignalStatus.UNDER_REVIEW.value,
        within_detection_window,
    )
    rows = (
        await session.execute(
            select(SignalCluster, ClusteringPolicy)
            .join(ClusteringPolicy, ClusteringPolicy.id == SignalCluster.policy_id)
            .where(
                SignalCluster.status.in_(ACTIVE_SIGNAL_STATUSES),
                visible_signal,
            )
            .order_by(SignalCluster.last_report_at.desc(), SignalCluster.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    hour_bucket = func.date_trunc("hour", Report.received_at).label("bucket_start")
    hourly_rows = (
        await session.execute(
            select(
                hour_bucket,
                func.count(SignalMember.report_id).label("raw_report_count"),
                func.count(func.distinct(Report.session_digest)).label("reporting_unique_sessions"),
            )
            .join(SignalMember, SignalMember.report_id == Report.id)
            .join(SignalCluster, SignalCluster.id == SignalMember.signal_id)
            .join(ClusteringPolicy, ClusteringPolicy.id == SignalCluster.policy_id)
            .where(
                SignalCluster.status.in_(ACTIVE_SIGNAL_STATUSES),
                visible_signal,
            )
            .group_by("bucket_start")
            .order_by("bucket_start")
        )
    ).all()
    active_policy = await session.scalar(
        select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
    )
    return SignalDashboardResponse(
        updated_at=now,
        items=[
            SignalDashboardItem(
                signal_id=cluster.id,
                status=_public_signal_status(cluster.status),
                channel=cluster.channel,
                feature_area=cluster.feature_area,
                reported_symptom_type=cluster.reported_symptom_type,
                reporting_unique_sessions=cluster.reporting_unique_sessions,
                raw_report_count=cluster.raw_report_count,
                review_priority=cluster.review_priority,
                first_report_at=cluster.first_report_at,
                last_report_at=cluster.last_report_at,
                affected_features=[cluster.feature_area],
                policy_version=policy.policy_version,
                policy_status=policy.status,
                baseline_status=BaselineStatus.INSUFFICIENT_HISTORY,
                baseline_ratio=None,
                official_incident=False,
                official_notice_url=cluster.official_notice_url,
            )
            for cluster, policy in rows
        ],
        hourly_volume=[
            SignalHourlyVolume(
                bucket_start=bucket_start,
                raw_report_count=raw_report_count,
                reporting_unique_sessions=reporting_unique_sessions,
            )
            for bucket_start, raw_report_count, reporting_unique_sessions in hourly_rows
        ],
        applied_policy=(
            SignalPolicySnapshot(
                policy_version=active_policy.policy_version,
                status=ClusteringPolicyStatus(active_policy.status),
                window_seconds=active_policy.window_seconds,
                min_unique_sessions=active_policy.min_unique_sessions,
                review_priority_threshold=active_policy.review_priority_threshold,
                similarity_threshold=active_policy.similarity_threshold,
                structured_rules_version=active_policy.structured_rules_version,
                taxonomy_version=active_policy.taxonomy_version,
                baseline_policy_version=active_policy.baseline_policy_version,
            )
            if active_policy is not None
            else None
        ),
        baseline_status=BaselineStatus.INSUFFICIENT_HISTORY,
        baseline_ratio=None,
        limit=limit,
        offset=offset,
    )


async def enforce_dashboard_rate_limit(
    session: AsyncSession,
    principal_digest: bytes,
    client_identifier: str,
    settings: Settings,
    *,
    now: datetime,
) -> None:
    client_fingerprint = keyed_fingerprint(
        client_identifier,
        "signal-dashboard-client",
        settings.rate_limit_hmac_key.get_secret_value().encode(),
    )
    async with session.begin():
        rate = await consume_rate_limit(
            session,
            scope=RateLimitScope.SIGNAL_DASHBOARD,
            principal_fingerprint=principal_digest,
            client_fingerprint=client_fingerprint,
            now=now,
            window_seconds=settings.signal_dashboard_window_seconds,
        )
    if rate.count > settings.signal_dashboard_limit:
        raise rate_limit_error(rate.expires_at, now)


async def related_signals_for_report(
    session: AsyncSession,
    report_id: UUID,
) -> list[RelatedSignal]:
    clusters = list(
        (
            await session.scalars(
                select(SignalCluster)
                .join(SignalMember, SignalMember.signal_id == SignalCluster.id)
                .where(
                    SignalMember.report_id == report_id,
                    SignalCluster.status.in_(ACTIVE_SIGNAL_STATUSES),
                )
                .order_by(SignalCluster.last_report_at.desc())
            )
        ).all()
    )
    return [
        RelatedSignal(
            signal_id=cluster.id,
            status=_public_signal_status(cluster.status),
            reported_symptom_type=IssueType(cluster.reported_symptom_type),
            reporting_unique_sessions=cluster.reporting_unique_sessions,
            last_report_at=cluster.last_report_at,
            official_incident=False,
        )
        for cluster in clusters
    ]


def _mutation_response(cluster: SignalCluster, now: datetime) -> OperatorSignalMutationResponse:
    return OperatorSignalMutationResponse(
        signal_id=cluster.id,
        status=SignalStatus(cluster.status),
        closure_reason=(
            SignalClosureReason(cluster.closure_reason) if cluster.closure_reason else None
        ),
        reporting_unique_sessions=cluster.reporting_unique_sessions,
        raw_report_count=cluster.raw_report_count,
        official_notice_url=cluster.official_notice_url,
        changed_at=now,
    )


async def _idempotency_replay(
    session: AsyncSession,
    *,
    principal_digest: bytes,
    operation: str,
    client_request_id: UUID,
    request_sha256: str,
) -> bool:
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.principal_digest == principal_digest,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.client_request_id == client_request_id,
        )
    )
    if record is None:
        return False
    if not hmac.compare_digest(record.payload_sha256, request_sha256):
        raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "요청 ID가 다른 내용에 사용됐습니다.")
    return True


async def acknowledge_signal(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: OperatorAcknowledgeSignalRequest,
    *,
    now: datetime,
) -> OperatorSignalMutationResponse:
    operation = "ACKNOWLEDGE_SIGNAL"
    principal_digest = _operator_digest(principal.agent_id)
    request_sha256 = payload_sha256(request)
    async with session.begin():
        await lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await _idempotency_replay(
            session,
            principal_digest=principal_digest,
            operation=operation,
            client_request_id=request.client_request_id,
            request_sha256=request_sha256,
        )
        cluster = await session.scalar(
            select(SignalCluster).where(SignalCluster.id == request.signal_id).with_for_update()
        )
        if cluster is None:
            raise ServiceError(404, "SIGNAL_NOT_FOUND", "장애 의심 신호를 찾을 수 없습니다.")
        if replay:
            return _mutation_response(cluster, now)
        if cluster.status != SignalStatus.SIGNAL_DETECTED.value:
            raise ServiceError(
                409,
                "INVALID_SIGNAL_STATE",
                "현재 상태에서는 검토를 시작할 수 없습니다.",
            )
        before = cluster.status
        cluster.status = SignalStatus.UNDER_REVIEW.value
        session.add_all(
            (
                completed_idempotency_record(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=request_sha256,
                    response_status=200,
                    now=now,
                ),
                _audit_event(
                    signal_id=cluster.id,
                    actor_id=principal.agent_id,
                    actor_type="operator",
                    action="SIGNAL_ACKNOWLEDGED",
                    now=now,
                    before_status=before,
                    after_status=cluster.status,
                    reason=request.reason,
                ),
            )
        )
        return _mutation_response(cluster, now)


async def close_signal(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: OperatorCloseSignalRequest,
    *,
    now: datetime,
) -> OperatorSignalMutationResponse:
    operation = "CLOSE_SIGNAL"
    principal_digest = _operator_digest(principal.agent_id)
    request_sha256 = payload_sha256(request)
    async with session.begin():
        await lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await _idempotency_replay(
            session,
            principal_digest=principal_digest,
            operation=operation,
            client_request_id=request.client_request_id,
            request_sha256=request_sha256,
        )
        cluster = await session.scalar(
            select(SignalCluster).where(SignalCluster.id == request.signal_id).with_for_update()
        )
        if cluster is None:
            raise ServiceError(404, "SIGNAL_NOT_FOUND", "장애 의심 신호를 찾을 수 없습니다.")
        if replay:
            return _mutation_response(cluster, now)
        if cluster.status == SignalStatus.CLOSED.value:
            raise ServiceError(409, "INVALID_SIGNAL_STATE", "이미 종료된 신호입니다.")
        before = cluster.status
        cluster.status = SignalStatus.CLOSED.value
        cluster.closure_reason = request.closure_reason.value
        cluster.closed_at = now
        session.add_all(
            (
                completed_idempotency_record(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=request_sha256,
                    response_status=200,
                    now=now,
                ),
                _audit_event(
                    signal_id=cluster.id,
                    actor_id=principal.agent_id,
                    actor_type="operator",
                    action="SIGNAL_CLOSED",
                    now=now,
                    before_status=before,
                    after_status=cluster.status,
                    reason=request.closure_reason.value,
                ),
            )
        )
        return _mutation_response(cluster, now)


async def link_official_notice(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: OperatorOfficialNoticeRequest,
    *,
    now: datetime,
) -> OperatorSignalMutationResponse:
    operation = "LINK_SIGNAL_NOTICE"
    principal_digest = _operator_digest(principal.agent_id)
    request_sha256 = payload_sha256(request)
    async with session.begin():
        await lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await _idempotency_replay(
            session,
            principal_digest=principal_digest,
            operation=operation,
            client_request_id=request.client_request_id,
            request_sha256=request_sha256,
        )
        cluster = await session.scalar(
            select(SignalCluster).where(SignalCluster.id == request.signal_id).with_for_update()
        )
        if cluster is None:
            raise ServiceError(404, "SIGNAL_NOT_FOUND", "장애 의심 신호를 찾을 수 없습니다.")
        if replay:
            return _mutation_response(cluster, now)
        cluster.official_notice_url = request.official_notice_url
        cluster.official_notice_linked_at = now
        cluster.official_notice_linked_by = principal.agent_id
        session.add_all(
            (
                completed_idempotency_record(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=request_sha256,
                    response_status=200,
                    now=now,
                ),
                _audit_event(
                    signal_id=cluster.id,
                    actor_id=principal.agent_id,
                    actor_type="operator",
                    action="OFFICIAL_NOTICE_LINKED",
                    now=now,
                    before_status=cluster.status,
                    after_status=cluster.status,
                ),
            )
        )
        return _mutation_response(cluster, now)


def _same_automatic_gate(source: SignalCluster, target: SignalCluster) -> bool:
    if (
        source.policy_id != target.policy_id
        or source.channel != target.channel
        or source.feature_area != target.feature_area
        or source.reported_symptom_type != target.reported_symptom_type
    ):
        return False
    if (
        source.submission_status_filter is not None
        and target.submission_status_filter is not None
        and source.submission_status_filter != target.submission_status_filter
    ):
        return False
    return not (
        source.error_code_filter is not None
        and target.error_code_filter is not None
        and source.error_code_filter != target.error_code_filter
    )


async def merge_signals(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: OperatorMergeSignalsRequest,
    *,
    now: datetime,
) -> OperatorSignalMutationResponse:
    operation = "MERGE_SIGNALS"
    principal_digest = _operator_digest(principal.agent_id)
    request_sha256 = payload_sha256(request)
    async with session.begin():
        await lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await _idempotency_replay(
            session,
            principal_digest=principal_digest,
            operation=operation,
            client_request_id=request.client_request_id,
            request_sha256=request_sha256,
        )
        clusters = list(
            (
                await session.scalars(
                    select(SignalCluster)
                    .where(
                        SignalCluster.id.in_((request.source_signal_id, request.target_signal_id))
                    )
                    .order_by(SignalCluster.id)
                    .with_for_update()
                )
            ).all()
        )
        by_id = {cluster.id: cluster for cluster in clusters}
        source = by_id.get(request.source_signal_id)
        target = by_id.get(request.target_signal_id)
        if source is None or target is None:
            raise ServiceError(404, "SIGNAL_NOT_FOUND", "장애 의심 신호를 찾을 수 없습니다.")
        if replay:
            return _mutation_response(target, now)
        if source.status == SignalStatus.CLOSED.value or target.status == SignalStatus.CLOSED.value:
            raise ServiceError(409, "INVALID_SIGNAL_STATE", "종료된 신호는 병합할 수 없습니다.")
        if not _same_automatic_gate(source, target):
            raise ServiceError(
                422,
                "SIGNAL_GATE_CONFLICT",
                "구조화 필드 또는 정책이 다른 신호는 병합할 수 없습니다.",
            )
        source_members = list(
            (
                await session.scalars(
                    select(SignalMember)
                    .where(SignalMember.signal_id == source.id)
                    .order_by(SignalMember.id)
                    .with_for_update()
                )
            ).all()
        )
        target_report_ids = set(
            (
                await session.scalars(
                    select(SignalMember.report_id).where(SignalMember.signal_id == target.id)
                )
            ).all()
        )
        for member in source_members:
            if member.report_id in target_report_ids:
                await session.delete(member)
            else:
                member.signal_id = target.id
                target_report_ids.add(member.report_id)
        source_before = source.status
        source.status = SignalStatus.CLOSED.value
        source.closure_reason = SignalClosureReason.MERGED.value
        source.closed_at = now
        source.raw_report_count = 0
        source.reporting_unique_sessions = 0
        source.review_priority = False
        source.representative_symptom_id = None
        if target.submission_status_filter is None:
            target.submission_status_filter = source.submission_status_filter
        if target.error_code_filter is None:
            target.error_code_filter = source.error_code_filter
        await session.flush()
        await _recalculate_cluster(
            session,
            target,
            now=now,
            deletion_recalculation=False,
        )
        session.add_all(
            (
                completed_idempotency_record(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=request_sha256,
                    response_status=200,
                    now=now,
                ),
                _audit_event(
                    signal_id=source.id,
                    actor_id=principal.agent_id,
                    actor_type="operator",
                    action="SIGNAL_MERGED",
                    now=now,
                    before_status=source_before,
                    after_status=source.status,
                    reason=request.reason,
                    target_signal_id=target.id,
                ),
            )
        )
        return _mutation_response(target, now)


async def split_signal(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: OperatorSplitSignalRequest,
    *,
    now: datetime,
) -> OperatorSignalMutationResponse:
    operation = "SPLIT_SIGNAL"
    principal_digest = _operator_digest(principal.agent_id)
    request_sha256 = payload_sha256(request)
    async with session.begin():
        await lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await _idempotency_replay(
            session,
            principal_digest=principal_digest,
            operation=operation,
            client_request_id=request.client_request_id,
            request_sha256=request_sha256,
        )
        source = await session.scalar(
            select(SignalCluster).where(SignalCluster.id == request.signal_id).with_for_update()
        )
        if source is None:
            raise ServiceError(404, "SIGNAL_NOT_FOUND", "장애 의심 신호를 찾을 수 없습니다.")
        if replay:
            return _mutation_response(source, now)
        if source.status == SignalStatus.CLOSED.value:
            raise ServiceError(409, "INVALID_SIGNAL_STATE", "종료된 신호는 분리할 수 없습니다.")
        members = list(
            (
                await session.scalars(
                    select(SignalMember)
                    .where(SignalMember.signal_id == source.id)
                    .order_by(SignalMember.id)
                    .with_for_update()
                )
            ).all()
        )
        selected_ids = set(request.report_ids)
        selected = [member for member in members if member.report_id in selected_ids]
        if len(selected) != len(selected_ids):
            raise ServiceError(422, "SIGNAL_MEMBER_NOT_FOUND", "분리할 제보를 찾을 수 없습니다.")
        if len(selected) == len(members):
            raise ServiceError(
                422,
                "EMPTY_SOURCE_SIGNAL",
                "모든 제보를 한 번에 분리할 수 없습니다.",
            )
        reports = list(
            (
                await session.scalars(
                    select(Report).where(Report.id.in_(selected_ids)).order_by(Report.id)
                )
            ).all()
        )
        if not reports:
            raise ServiceError(422, "SIGNAL_MEMBER_NOT_FOUND", "분리할 제보를 찾을 수 없습니다.")
        new_cluster = SignalCluster(
            policy_id=source.policy_id,
            status=SignalStatus.CANDIDATE.value,
            channel=source.channel,
            feature_area=source.feature_area,
            reported_symptom_type=source.reported_symptom_type,
            submission_status_filter=source.submission_status_filter,
            error_code_filter=source.error_code_filter,
            raw_report_count=0,
            reporting_unique_sessions=0,
            review_priority=False,
            first_report_at=min(report.received_at for report in reports),
            last_report_at=max(report.received_at for report in reports),
            representative_symptom_id=None,
            official_incident=False,
            created_at=now,
            updated_at=now,
            purge_at=max(report.purge_at for report in reports),
        )
        session.add(new_cluster)
        await session.flush()
        for member in selected:
            member.signal_id = new_cluster.id
        await session.flush()
        await _recalculate_cluster(
            session,
            source,
            now=now,
            deletion_recalculation=True,
        )
        await _recalculate_cluster(
            session,
            new_cluster,
            now=now,
            deletion_recalculation=False,
        )
        session.add_all(
            (
                completed_idempotency_record(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=request_sha256,
                    response_status=200,
                    now=now,
                ),
                _audit_event(
                    signal_id=source.id,
                    actor_id=principal.agent_id,
                    actor_type="operator",
                    action="SIGNAL_SPLIT",
                    now=now,
                    before_status=source.status,
                    after_status=source.status,
                    reason=request.reason,
                    target_signal_id=new_cluster.id,
                ),
            )
        )
        return _mutation_response(source, now)
