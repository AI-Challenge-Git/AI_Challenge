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
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.codes import (
    SUPPORTED_BASELINE_POLICY_VERSION,
    AuditOutcome,
    BaselineStatus,
    ClusteringLinkageMethod,
    ClusteringPolicyStatus,
    ClusterRepresentativeMethod,
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
    AuditLog,
    ClusteringPolicy,
    IdempotencyRecord,
    Report,
    SignalAuditEvent,
    SignalCluster,
    SignalMember,
    SignalProcessingJob,
    SignalRelevanceLock,
    TechnicalEmbedding,
    TechnicalSymptom,
    Vector1024,
)
from app.schemas import (
    OperatorAcknowledgeSignalRequest,
    OperatorApproveSignalPolicyRequest,
    OperatorCloseSignalRequest,
    OperatorMergeSignalsRequest,
    OperatorOfficialNoticeRequest,
    OperatorSignalListItem,
    OperatorSignalListResponse,
    OperatorSignalMutationResponse,
    OperatorSignalPolicyApprovalResponse,
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
from app.signal_relevance import (
    CustomerSignalCandidate,
    IncidentSignal,
    SignalRelevanceResult,
    evaluate_signal_relevance,
)

if TYPE_CHECKING:
    from app.services.agents import AgentPrincipal

SAFE_PROCESSING_ERRORS = {
    "EMBEDDING_UNAVAILABLE",
    "INVALID_EMBEDDING",
    "POLICY_MISMATCH",
    "EMBEDDING_INPUT_UNAVAILABLE",
    "RETRY_EXHAUSTED",
}
PERMANENT_PROCESSING_ERRORS = {
    "INVALID_EMBEDDING",
    "POLICY_MISMATCH",
    "EMBEDDING_INPUT_UNAVAILABLE",
}
AUTO_CLUSTER_EXCLUDED_ISSUE_TYPES = {
    IssueType.UNKNOWN.value,
    IssueType.UNRELATED_OR_AMBIGUOUS.value,
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


def _public_signal_filter(now: datetime) -> ColumnElement[bool]:
    within_detection_window = (
        SignalCluster.last_report_at + ClusteringPolicy.window_seconds * text("INTERVAL '1 second'")
        >= now
    )
    return or_(
        SignalCluster.status == SignalStatus.UNDER_REVIEW.value,
        within_detection_window,
    )


def _signal_window_expires_at(cluster: SignalCluster, policy: ClusteringPolicy) -> datetime:
    return cluster.last_report_at + timedelta(seconds=policy.window_seconds)


def _is_public_signal(
    cluster: SignalCluster,
    policy: ClusteringPolicy,
    *,
    now: datetime,
) -> bool:
    return cluster.status == SignalStatus.UNDER_REVIEW.value or (
        cluster.status == SignalStatus.SIGNAL_DETECTED.value
        and _signal_window_expires_at(cluster, policy) >= now
    )


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


def _policy_resource_fingerprint(policy_version: str) -> str:
    return hashlib.sha256(f"signal-policy:{policy_version}".encode()).hexdigest()


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


def is_signal_processing_eligible(*, issue_type: str, symptom: str | None) -> bool:
    return symptom is not None and issue_type not in AUTO_CLUSTER_EXCLUDED_ISSUE_TYPES


async def _mark_job_failed(
    session: AsyncSession,
    job_id: UUID,
    safe_error_code: str,
    *,
    now: datetime,
    max_attempts: int,
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
        terminal = (
            safe_error_code in PERMANENT_PROCESSING_ERRORS or job.attempt_count >= max_attempts
        )
        job.status = (
            SignalProcessingStatus.DEAD_LETTER.value
            if terminal
            else SignalProcessingStatus.FAILED.value
        )
        job.safe_error_code = (
            "RETRY_EXHAUSTED"
            if terminal and safe_error_code not in PERMANENT_PROCESSING_ERRORS
            else safe_error_code
        )
        job.next_attempt_at = now if terminal else now + timedelta(minutes=5)
        job.completed_at = now
    return SignalProcessingResult(
        job_id=job_id,
        status=SignalProcessingStatus(job.status),
        signal_id=None,
        safe_error_code=job.safe_error_code,
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


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _average_linkage_similarity(
    left: list[list[float]],
    right: list[list[float]],
) -> float:
    return math.fsum(
        _cosine_similarity(left_vector, right_vector)
        for left_vector in left
        for right_vector in right
    ) / (len(left) * len(right))


async def _reconcile_average_linkage_clusters(
    session: AsyncSession,
    policy: ClusteringPolicy,
    symptom: TechnicalSymptom,
    *,
    report_id: UUID,
    anchor: datetime,
    now: datetime,
) -> SignalCluster | None:
    if policy.linkage_method != ClusteringLinkageMethod.AVERAGE.value:
        return None
    window_delta = timedelta(seconds=policy.window_seconds)
    known_submission = _known_submission_filter(symptom.submission_status)
    cluster_filters = [
        SignalCluster.policy_id == policy.id,
        SignalCluster.status.in_(
            (SignalStatus.CANDIDATE.value, SignalStatus.SIGNAL_DETECTED.value)
        ),
        SignalCluster.official_incident.is_(False),
        SignalCluster.channel == symptom.channel,
        SignalCluster.feature_area == symptom.feature_area,
        SignalCluster.reported_symptom_type == symptom.issue_type,
        SignalCluster.first_report_at <= anchor + window_delta,
        SignalCluster.last_report_at >= anchor - window_delta,
        ~select(SignalRelevanceLock.id)
        .where(SignalRelevanceLock.signal_id == SignalCluster.id)
        .exists(),
    ]
    if known_submission is not None:
        cluster_filters.append(
            (SignalCluster.submission_status_filter.is_(None))
            | (SignalCluster.submission_status_filter == known_submission)
        )
    if symptom.error_code is not None:
        cluster_filters.append(
            (SignalCluster.error_code_filter.is_(None))
            | (SignalCluster.error_code_filter == symptom.error_code)
        )
    clusters = list(
        (
            await session.scalars(
                select(SignalCluster)
                .where(*cluster_filters)
                .order_by(SignalCluster.id)
                .with_for_update()
            )
        ).all()
    )
    if len(clusters) < 2:
        return None

    cluster_by_id = {cluster.id: cluster for cluster in clusters}
    vectors_by_cluster: dict[UUID, list[list[float]]] = {cluster.id: [] for cluster in clusters}
    vector_rows = (
        await session.execute(
            select(SignalMember.signal_id, TechnicalEmbedding.embedding)
            .join(TechnicalEmbedding, TechnicalEmbedding.id == SignalMember.embedding_id)
            .join(Report, Report.id == SignalMember.report_id)
            .where(
                SignalMember.signal_id.in_(cluster_by_id),
                Report.received_at >= anchor - window_delta,
                Report.received_at <= anchor + window_delta,
            )
            .order_by(SignalMember.signal_id, SignalMember.id)
        )
    ).all()
    for signal_id, vector in vector_rows:
        vectors_by_cluster[signal_id].append(list(vector))

    # ponytail: exact O(n^2) scan is bounded by the 600-second gate window; move pair scoring to
    # batched SQL only when production volume proves this worker-side calculation is too slow.
    while True:
        best: tuple[float, UUID, UUID] | None = None
        active_ids = sorted(
            signal_id for signal_id, vectors in vectors_by_cluster.items() if vectors
        )
        for index, left_id in enumerate(active_ids):
            for right_id in active_ids[index + 1 :]:
                if not _same_automatic_gate(cluster_by_id[left_id], cluster_by_id[right_id]):
                    continue
                score = _average_linkage_similarity(
                    vectors_by_cluster[left_id], vectors_by_cluster[right_id]
                )
                candidate = (score, left_id, right_id)
                if (
                    best is None
                    or score > best[0] + COSINE_COMPARISON_TOLERANCE
                    or (
                        abs(score - best[0]) <= COSINE_COMPARISON_TOLERANCE
                        and (left_id, right_id) < (best[1], best[2])
                    )
                ):
                    best = candidate
        if best is None or best[0] < policy.similarity_threshold - COSINE_COMPARISON_TOLERANCE:
            break

        _, target_id, source_id = best
        target = cluster_by_id[target_id]
        source = cluster_by_id[source_id]
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
        for member in source_members:
            member.signal_id = target.id
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
        vectors_by_cluster[target_id].extend(vectors_by_cluster.pop(source_id))
        await session.flush()
        await _recalculate_cluster(
            session,
            target,
            now=now,
            deletion_recalculation=False,
        )
        session.add(
            _audit_event(
                signal_id=source.id,
                action="SIGNAL_AUTOMATICALLY_MERGED",
                now=now,
                before_status=source_before,
                after_status=source.status,
                reason="AVERAGE_LINKAGE_THRESHOLD",
                target_signal_id=target.id,
            )
        )
    current_signal_id = await session.scalar(
        select(SignalMember.signal_id).where(SignalMember.report_id == report_id)
    )
    return cluster_by_id.get(current_signal_id) if current_signal_id is not None else None


async def _recalculate_cluster(
    session: AsyncSession,
    cluster: SignalCluster,
    *,
    now: datetime,
    deletion_recalculation: bool,
) -> None:
    policy = await session.get(ClusteringPolicy, cluster.policy_id)
    if policy is None:
        raise RuntimeError("signal policy is missing")
    latest_report_at = await session.scalar(
        select(func.max(Report.received_at))
        .select_from(SignalMember)
        .join(Report, Report.id == SignalMember.report_id)
        .where(SignalMember.signal_id == cluster.id)
    )
    window_start = (
        latest_report_at - timedelta(seconds=policy.window_seconds)
        if latest_report_at is not None
        else None
    )
    counts = (
        (
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
                .where(
                    SignalMember.signal_id == cluster.id,
                    Report.received_at >= window_start,
                    Report.received_at <= latest_report_at,
                )
            )
        ).one()
        if latest_report_at is not None
        else (0, 0, None, None, None)
    )
    raw_count = int(counts[0] or 0)
    unique_sessions = int(counts[1] or 0)

    before = cluster.status
    cluster.raw_report_count = raw_count
    cluster.reporting_unique_sessions = unique_sessions
    cluster.review_priority = unique_sessions >= policy.review_priority_threshold
    cluster.representative_symptom_id = None
    first_report_at, last_report_at, purge_at = counts[2], counts[3], counts[4]
    if (
        isinstance(first_report_at, datetime)
        and isinstance(last_report_at, datetime)
        and isinstance(purge_at, datetime)
    ):
        cluster.first_report_at = first_report_at
        cluster.last_report_at = last_report_at
        cluster.purge_at = purge_at

    if unique_sessions < policy.min_unique_sessions and deletion_recalculation:
        cluster.status = SignalStatus.CLOSED.value
        cluster.closure_reason = SignalClosureReason.EVIDENCE_RECALCULATED.value
        cluster.closed_at = now
    elif cluster.status == SignalStatus.CANDIDATE.value and (
        unique_sessions >= policy.min_unique_sessions
    ):
        cluster.status = SignalStatus.SIGNAL_DETECTED.value
    elif (
        cluster.status == SignalStatus.SIGNAL_DETECTED.value
        and unique_sessions < policy.min_unique_sessions
    ):
        cluster.status = SignalStatus.CANDIDATE.value

    if (
        raw_count > 0
        and cluster.status != SignalStatus.CLOSED.value
        and policy.representative_method == ClusterRepresentativeMethod.MEDOID.value
    ):
        left_member = aliased(SignalMember)
        right_member = aliased(SignalMember)
        left_embedding = aliased(TechnicalEmbedding)
        right_embedding = aliased(TechnicalEmbedding)
        left_report = aliased(Report)
        right_report = aliased(Report)
        left_vector = (
            cast(left_embedding.embedding, Vector1024())
            if policy.embedding_dimension == 1024
            else left_embedding.embedding
        )
        right_vector = (
            cast(right_embedding.embedding, Vector1024())
            if policy.embedding_dimension == 1024
            else right_embedding.embedding
        )
        distance = left_vector.op("<=>", return_type=Float())(right_vector)
        average_similarity = func.avg(1.0 - distance)
        cluster.representative_symptom_id = await session.scalar(
            select(left_member.technical_symptom_id)
            .join(left_embedding, left_embedding.id == left_member.embedding_id)
            .join(left_report, left_report.id == left_member.report_id)
            .join(right_member, right_member.signal_id == left_member.signal_id)
            .join(right_embedding, right_embedding.id == right_member.embedding_id)
            .join(right_report, right_report.id == right_member.report_id)
            .where(
                left_member.signal_id == cluster.id,
                left_report.received_at >= window_start,
                left_report.received_at <= latest_report_at,
                right_report.received_at >= window_start,
                right_report.received_at <= latest_report_at,
            )
            .group_by(left_member.technical_symptom_id)
            .order_by(average_similarity.desc(), left_member.technical_symptom_id)
            .limit(1)
        )

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
    max_attempts: int = 5,
) -> SignalProcessingResult | None:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
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
        if job.attempt_count >= max_attempts:
            job.status = SignalProcessingStatus.DEAD_LETTER.value
            job.safe_error_code = "RETRY_EXHAUSTED"
            job.next_attempt_at = now
            job.completed_at = now
            return SignalProcessingResult(
                job_id=job.id,
                status=SignalProcessingStatus.DEAD_LETTER,
                signal_id=None,
                safe_error_code="RETRY_EXHAUSTED",
            )
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
            max_attempts=max_attempts,
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
            max_attempts=max_attempts,
        )
    except Exception:  # provider exceptions are never exposed outside this boundary
        return await _mark_job_failed(
            session,
            job_id,
            "EMBEDDING_UNAVAILABLE",
            now=now,
            max_attempts=max_attempts,
        )

    async with session.begin():
        policy = await session.get(ClusteringPolicy, policy_id)
        job = await session.scalar(
            select(SignalProcessingJob).where(SignalProcessingJob.id == job_id).with_for_update()
        )
        if policy is None or job is None:
            raise RuntimeError("signal processing state disappeared")
        if not _validate_embedding_metadata(result, policy):
            job.status = SignalProcessingStatus.DEAD_LETTER.value
            job.safe_error_code = "POLICY_MISMATCH"
            job.next_attempt_at = now
            job.completed_at = now
            return SignalProcessingResult(
                job_id=job.id,
                status=SignalProcessingStatus.DEAD_LETTER,
                signal_id=None,
                safe_error_code="POLICY_MISMATCH",
            )
        symptom = await session.get(TechnicalSymptom, job.technical_symptom_id)
        report = await session.get(Report, job.report_id)
        if symptom is None or report is None:
            return None
        if symptom.taxonomy_version != policy.taxonomy_version:
            job.status = SignalProcessingStatus.DEAD_LETTER.value
            job.safe_error_code = "POLICY_MISMATCH"
            job.next_attempt_at = now
            job.completed_at = now
            return SignalProcessingResult(
                job_id=job.id,
                status=SignalProcessingStatus.DEAD_LETTER,
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
        member_report = aliased(Report)
        window_delta = timedelta(seconds=policy.window_seconds)
        distance = embedding_expression.op("<=>", return_type=Float())(result.vector)
        candidate_filters = [
            SignalCluster.policy_id == policy.id,
            SignalCluster.status != SignalStatus.CLOSED.value,
            SignalCluster.channel == symptom.channel,
            SignalCluster.feature_area == symptom.feature_area,
            SignalCluster.reported_symptom_type == symptom.issue_type,
            SignalCluster.first_report_at <= report.received_at + window_delta,
            SignalCluster.last_report_at >= report.received_at - window_delta,
            member_report.received_at >= report.received_at - window_delta,
            member_report.received_at <= report.received_at + window_delta,
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
        similarity_expression = (1.0 - distance).label("similarity")
        if policy.linkage_method == ClusteringLinkageMethod.AVERAGE.value:
            cluster_scores = (
                select(
                    SignalMember.signal_id.label("signal_id"),
                    func.avg(similarity_expression).label("similarity"),
                )
                .join(SignalCluster, SignalCluster.id == SignalMember.signal_id)
                .join(TechnicalEmbedding, TechnicalEmbedding.id == SignalMember.embedding_id)
                .join(member_report, member_report.id == SignalMember.report_id)
                .where(*candidate_filters)
                .group_by(SignalMember.signal_id)
                .subquery()
            )
            candidate_row = (
                await session.execute(
                    select(SignalCluster, cluster_scores.c.similarity)
                    .join(cluster_scores, cluster_scores.c.signal_id == SignalCluster.id)
                    .order_by(cluster_scores.c.similarity.desc(), SignalCluster.id)
                    .limit(1)
                )
            ).first()
        else:
            candidate_row = (
                await session.execute(
                    select(SignalCluster, similarity_expression)
                    .join(SignalMember, SignalMember.signal_id == SignalCluster.id)
                    .join(TechnicalEmbedding, TechnicalEmbedding.id == SignalMember.embedding_id)
                    .join(member_report, member_report.id == SignalMember.report_id)
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
        reconciled_cluster = await _reconcile_average_linkage_clusters(
            session,
            policy,
            symptom,
            report_id=report.id,
            anchor=report.received_at,
            now=now,
        )
        if reconciled_cluster is not None:
            cluster = reconciled_cluster
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
    visible_signal = _public_signal_filter(now)
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
    baseline_by_signal: dict[UUID, tuple[BaselineStatus, float | None, int]] = {}
    supported_signal_ids = [
        cluster.id
        for cluster, policy in rows
        if policy.baseline_policy_version == SUPPORTED_BASELINE_POLICY_VERSION
    ]
    previous_counts: dict[UUID, int] = {}
    if supported_signal_ids:
        window_interval = ClusteringPolicy.window_seconds * text("INTERVAL '1 second'")
        previous_rows = (
            await session.execute(
                select(
                    SignalMember.signal_id,
                    func.count(func.distinct(Report.session_digest)),
                )
                .join(Report, Report.id == SignalMember.report_id)
                .join(SignalCluster, SignalCluster.id == SignalMember.signal_id)
                .join(ClusteringPolicy, ClusteringPolicy.id == SignalCluster.policy_id)
                .where(
                    SignalMember.signal_id.in_(supported_signal_ids),
                    Report.received_at >= SignalCluster.last_report_at - 2 * window_interval,
                    Report.received_at < SignalCluster.last_report_at - window_interval,
                )
                .group_by(SignalMember.signal_id)
            )
        ).all()
        previous_counts = {signal_id: int(count) for signal_id, count in previous_rows}
    for cluster, policy in rows:
        previous_window_start = cluster.last_report_at - timedelta(
            seconds=2 * policy.window_seconds
        )
        previous_count = previous_counts.get(cluster.id, 0)
        if (
            policy.baseline_policy_version != SUPPORTED_BASELINE_POLICY_VERSION
            or policy.created_at > previous_window_start
        ):
            baseline_by_signal[cluster.id] = (
                BaselineStatus.INSUFFICIENT_HISTORY,
                None,
                previous_count,
            )
        elif previous_count == 0:
            baseline_by_signal[cluster.id] = (
                BaselineStatus.ZERO_BASELINE,
                None,
                0,
            )
        else:
            baseline_by_signal[cluster.id] = (
                BaselineStatus.AVAILABLE,
                cluster.reporting_unique_sessions / previous_count,
                previous_count,
            )
    hour_bucket = func.date_trunc("hour", Report.received_at).label("bucket_start")
    dashboard_window = ClusteringPolicy.window_seconds * text("INTERVAL '1 second'")
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
                Report.received_at >= SignalCluster.last_report_at - dashboard_window,
                Report.received_at <= SignalCluster.last_report_at,
            )
            .group_by("bucket_start")
            .order_by("bucket_start")
        )
    ).all()
    active_policy = await session.scalar(
        select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
    )
    baseline_values = [baseline_by_signal[cluster.id] for cluster, _ in rows]
    if not baseline_values or any(
        status is BaselineStatus.INSUFFICIENT_HISTORY for status, _, _ in baseline_values
    ):
        dashboard_baseline_status = BaselineStatus.INSUFFICIENT_HISTORY
        dashboard_baseline_ratio = None
    else:
        previous_total = sum(previous for _, _, previous in baseline_values)
        if previous_total == 0:
            dashboard_baseline_status = BaselineStatus.ZERO_BASELINE
            dashboard_baseline_ratio = None
        else:
            dashboard_baseline_status = BaselineStatus.AVAILABLE
            dashboard_baseline_ratio = (
                sum(cluster.reporting_unique_sessions for cluster, _ in rows) / previous_total
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
                baseline_status=baseline_by_signal[cluster.id][0],
                baseline_ratio=baseline_by_signal[cluster.id][1],
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
                linkage_method=ClusteringLinkageMethod(active_policy.linkage_method),
                representative_method=ClusterRepresentativeMethod(
                    active_policy.representative_method
                ),
                structured_rules_version=active_policy.structured_rules_version,
                taxonomy_version=active_policy.taxonomy_version,
                baseline_policy_version=active_policy.baseline_policy_version,
            )
            if active_policy is not None
            else None
        ),
        baseline_status=dashboard_baseline_status,
        baseline_ratio=dashboard_baseline_ratio,
        limit=limit,
        offset=offset,
    )


async def list_operator_signals(
    session: AsyncSession,
    *,
    now: datetime,
    status: SignalStatus | None,
    limit: int,
    offset: int,
) -> OperatorSignalListResponse:
    statement = select(SignalCluster, ClusteringPolicy).join(
        ClusteringPolicy,
        ClusteringPolicy.id == SignalCluster.policy_id,
    )
    if status is not None:
        statement = statement.where(SignalCluster.status == status.value)
    rows = (
        await session.execute(
            statement.order_by(SignalCluster.last_report_at.desc(), SignalCluster.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return OperatorSignalListResponse(
        updated_at=now,
        items=[
            OperatorSignalListItem(
                signal_id=cluster.id,
                status=SignalStatus(cluster.status),
                closure_reason=(
                    SignalClosureReason(cluster.closure_reason)
                    if cluster.closure_reason is not None
                    else None
                ),
                channel=cluster.channel,
                feature_area=cluster.feature_area,
                reported_symptom_type=IssueType(cluster.reported_symptom_type),
                reporting_unique_sessions=cluster.reporting_unique_sessions,
                raw_report_count=cluster.raw_report_count,
                review_priority=cluster.review_priority,
                first_report_at=cluster.first_report_at,
                last_report_at=cluster.last_report_at,
                window_expires_at=_signal_window_expires_at(cluster, policy),
                public_visible=_is_public_signal(cluster, policy, now=now),
                policy_version=policy.policy_version,
                policy_status=ClusteringPolicyStatus(policy.status),
                official_notice_url=cluster.official_notice_url,
                closed_at=cluster.closed_at,
            )
            for cluster, policy in rows
        ],
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
    *,
    now: datetime,
) -> list[RelatedSignal]:
    clusters = list(
        (
            await session.scalars(
                select(SignalCluster)
                .join(ClusteringPolicy, ClusteringPolicy.id == SignalCluster.policy_id)
                .join(SignalMember, SignalMember.signal_id == SignalCluster.id)
                .where(
                    SignalMember.report_id == report_id,
                    SignalCluster.status.in_(ACTIVE_SIGNAL_STATUSES),
                    _public_signal_filter(now),
                )
                .order_by(SignalCluster.last_report_at.desc())
            )
        ).all()
    )
    related = []
    for cluster in clusters:
        relevance_and_lock = await signal_relevance_for_report(
            session,
            report_id,
            cluster.id,
            now=now,
        )
        relevance = relevance_and_lock[0] if relevance_and_lock is not None else None
        locked = relevance_and_lock[1] if relevance_and_lock is not None else None
        related.append(
            RelatedSignal(
                signal_id=cluster.id,
                status=_public_signal_status(cluster.status),
                reported_symptom_type=IssueType(cluster.reported_symptom_type),
                reporting_unique_sessions=cluster.reporting_unique_sessions,
                last_report_at=cluster.last_report_at,
                official_incident=False,
                relevance_status=relevance.status if relevance is not None else None,
                confirmation_questions=(
                    list(relevance.confirmation_questions) if relevance is not None else []
                ),
                locked_related=locked.final_related if locked is not None else None,
            )
        )
    return related


async def has_candidate_signal_for_report(session: AsyncSession, report_id: UUID) -> bool:
    candidate_id = await session.scalar(
        select(SignalMember.id)
        .join(SignalCluster, SignalCluster.id == SignalMember.signal_id)
        .where(
            SignalMember.report_id == report_id,
            SignalCluster.status == SignalStatus.CANDIDATE.value,
        )
        .limit(1)
    )
    return candidate_id is not None


async def signal_relevance_for_report(
    session: AsyncSession,
    report_id: UUID,
    signal_id: UUID,
    *,
    now: datetime,
) -> tuple[SignalRelevanceResult, SignalRelevanceLock | None] | None:
    row = (
        await session.execute(
            select(
                SignalCluster,
                ClusteringPolicy,
                TechnicalSymptom,
                TechnicalEmbedding,
            )
            .join(ClusteringPolicy, ClusteringPolicy.id == SignalCluster.policy_id)
            .join(SignalMember, SignalMember.signal_id == SignalCluster.id)
            .join(TechnicalSymptom, TechnicalSymptom.id == SignalMember.technical_symptom_id)
            .join(TechnicalEmbedding, TechnicalEmbedding.id == SignalMember.embedding_id)
            .where(
                SignalCluster.id == signal_id,
                SignalMember.report_id == report_id,
                SignalCluster.status.in_(ACTIVE_SIGNAL_STATUSES),
                _public_signal_filter(now),
            )
        )
    ).first()
    if row is None:
        return None
    cluster, policy, symptom, customer_embedding = row
    if cluster.representative_symptom_id is None:
        return None
    representative_embedding = await session.scalar(
        select(TechnicalEmbedding).where(
            TechnicalEmbedding.technical_symptom_id == cluster.representative_symptom_id,
            TechnicalEmbedding.model_id == policy.model_id,
            TechnicalEmbedding.model_revision == policy.model_revision,
            TechnicalEmbedding.embedding_dimension == policy.embedding_dimension,
            TechnicalEmbedding.normalization == policy.normalization,
            TechnicalEmbedding.input_format == policy.input_format,
            TechnicalEmbedding.distance_metric == policy.distance_metric,
        )
    )
    if representative_embedding is None:
        return None
    relevance = evaluate_signal_relevance(
        CustomerSignalCandidate(
            report_id=str(report_id),
            issue_type=IssueType(symptom.issue_type),
            symptom_embedding=customer_embedding.embedding,
            reported_occurred_at=symptom.reported_occurred_at,
        ),
        IncidentSignal(
            signal_id=str(signal_id),
            issue_type=IssueType(cluster.reported_symptom_type),
            representative_embedding=representative_embedding.embedding,
            started_at=cluster.first_report_at,
            ended_at=cluster.last_report_at + timedelta(seconds=policy.window_seconds),
        ),
        threshold=policy.similarity_threshold,
    )
    locked = await session.scalar(
        select(SignalRelevanceLock).where(
            SignalRelevanceLock.report_id == report_id,
            SignalRelevanceLock.signal_id == signal_id,
        )
    )
    return relevance, locked


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


def _policy_approval_response(
    policy: ClusteringPolicy,
) -> OperatorSignalPolicyApprovalResponse:
    if (
        policy.approved_by is None
        or policy.approved_at is None
        or policy.approval_evidence_sha256 is None
    ):
        raise RuntimeError("approved policy metadata is incomplete")
    return OperatorSignalPolicyApprovalResponse(
        policy_version=policy.policy_version,
        status=ClusteringPolicyStatus(policy.status),
        approved_by=policy.approved_by,
        approved_at=policy.approved_at,
        evaluation_artifact_sha256=policy.approval_evidence_sha256,
    )


async def approve_signal_policy(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: OperatorApproveSignalPolicyRequest,
    *,
    now: datetime,
) -> OperatorSignalPolicyApprovalResponse:
    operation = "APPROVE_SIGNAL_POLICY"
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
        policy = await session.scalar(
            select(ClusteringPolicy)
            .where(ClusteringPolicy.policy_version == request.policy_version)
            .with_for_update()
        )
        if policy is None:
            raise ServiceError(404, "SIGNAL_POLICY_NOT_FOUND", "신호 정책을 찾을 수 없습니다.")
        if replay:
            return _policy_approval_response(policy)
        if policy.status != ClusteringPolicyStatus.EXPERIMENTAL.value:
            raise ServiceError(
                409,
                "INVALID_SIGNAL_POLICY_STATE",
                "실험 상태의 신호 정책만 승인할 수 있습니다.",
            )
        if not policy.is_active:
            raise ServiceError(
                409,
                "SIGNAL_POLICY_NOT_ACTIVE",
                "현재 활성 신호 정책만 승인할 수 있습니다.",
            )

        policy.status = ClusteringPolicyStatus.APPROVED.value
        policy.approved_by = principal.agent_id
        policy.approved_at = now
        policy.approval_evidence_sha256 = request.evaluation_artifact_sha256
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
                AuditLog(
                    actor_id=principal.agent_id,
                    actor_type="operator",
                    action="SIGNAL_POLICY_APPROVED",
                    outcome=AuditOutcome.SUCCESS.value,
                    resource_fingerprint=_policy_resource_fingerprint(policy.policy_version),
                    created_at=now,
                ),
            )
        )
        return _policy_approval_response(policy)


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
