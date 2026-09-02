import asyncio
import math
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, text

from app.api.dependencies import get_clock
from app.attachments import LocalAttachmentStore
from app.codes import (
    SUPPORTED_BASELINE_POLICY_VERSION,
    AgentRole,
    BaselineStatus,
    ClusteringLinkageMethod,
    ClusteringPolicyStatus,
    ClusterRepresentativeMethod,
    SignalClosureReason,
    SignalProcessingStatus,
    SignalStatus,
)
from app.config import Settings, get_settings
from app.db import engine, session_factory
from app.main import app
from app.models import (
    AgentAccessToken,
    AgentAccount,
    AgentSignalVerification,
    AuditLog,
    ClusteringPolicy,
    ConsultationCard,
    IdempotencyRecord,
    PolicySnapshot,
    RateLimitBucket,
    Report,
    SignalAuditEvent,
    SignalCluster,
    SignalMember,
    SignalProcessingJob,
    SignalRelevanceLock,
    SymbolMasterVersion,
    TechnicalEmbedding,
    TechnicalSymptom,
)
from app.schemas import SignalEmbeddingRequest, SignalEmbeddingResult
from app.security import hash_password, make_opaque_token, opaque_token_digest
from app.services import readiness
from app.services.lifecycle import purge_expired_data
from app.services.signal_embeddings import SignalEmbeddingContract
from app.services.signals import (
    detach_report_from_signals,
    enqueue_signal_processing,
    list_dashboard_signals,
    process_next_signal_job,
    related_signals_for_report,
)
from scripts import check_runtime_readiness
from scripts import process_signal_jobs as process_signal_jobs_script

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires migrated PostgreSQL with pgvector",
)

NOW = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)


class FakeEmbeddingProvider:
    def __init__(
        self,
        vector: list[float] | None = None,
        *,
        dimension: int = 3,
        model_id: str = "fake-embed",
    ) -> None:
        self.vector = vector or [1.0, 0.0, 0.0]
        self.dimension = dimension
        self.model_id = model_id
        self.calls = 0

    async def embed(self, request: SignalEmbeddingRequest) -> SignalEmbeddingResult:
        self.calls += 1
        assert request.technical_symptom
        return SignalEmbeddingResult(
            model_id=self.model_id,
            model_revision="test-r1",
            dimension=self.dimension,
            normalization="L2",
            input_format="query.v1",
            distance_metric="COSINE",
            vector=self.vector,
        )


class SequenceEmbeddingProvider:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = iter(vectors)

    async def embed(self, request: SignalEmbeddingRequest) -> SignalEmbeddingResult:
        assert request.technical_symptom
        return SignalEmbeddingResult(
            model_id="fake-embed",
            model_revision="test-r1",
            dimension=len(vector := next(self.vectors)),
            normalization="L2",
            input_format="query.v1",
            distance_metric="COSINE",
            vector=vector,
        )


class InvalidEmbeddingProvider:
    async def embed(self, request: SignalEmbeddingRequest) -> SignalEmbeddingResult:
        assert request.technical_symptom
        return cast(
            SignalEmbeddingResult,
            {
                "model_id": "fake-embed",
                "model_revision": "test-r1",
                "dimension": 3,
                "normalization": "L2",
                "input_format": "query.v1",
                "distance_metric": "COSINE",
                "vector": [1.0],
            },
        )


class FailingEmbeddingProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def embed(self, request: SignalEmbeddingRequest) -> SignalEmbeddingResult:
        assert request.technical_symptom
        raise self.error


async def _clean() -> None:
    async with session_factory() as session, session.begin():
        await session.execute(delete(RateLimitBucket))
        await session.execute(delete(Report))
        await session.execute(delete(SignalAuditEvent))
        await session.execute(delete(SignalCluster))
        await session.execute(delete(TechnicalEmbedding))
        await session.execute(delete(ClusteringPolicy))
        await session.execute(delete(SymbolMasterVersion))
        await session.execute(delete(IdempotencyRecord))
        await session.execute(delete(AuditLog))
        await session.execute(delete(AgentAccessToken))
        await session.execute(delete(AgentAccount))


@pytest.fixture(autouse=True)
async def clean_signal_data() -> AsyncIterator[None]:
    await _clean()
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_clock, None)
        await _clean()
        await engine.dispose()


async def _policy(
    *,
    min_unique_sessions: int = 2,
    dimension: int = 3,
    linkage_method: ClusteringLinkageMethod = ClusteringLinkageMethod.AVERAGE,
    representative_method: ClusterRepresentativeMethod = ClusterRepresentativeMethod.MEDOID,
    similarity_threshold: float = 0.79,
) -> UUID:
    async with session_factory() as session, session.begin():
        policy = ClusteringPolicy(
            policy_version=f"test-{uuid4()}",
            status=ClusteringPolicyStatus.EXPERIMENTAL.value,
            is_active=True,
            window_seconds=600,
            min_unique_sessions=min_unique_sessions,
            review_priority_threshold=max(3, min_unique_sessions),
            similarity_threshold=similarity_threshold,
            structured_rules_version="hard-gate.v1",
            model_id="fake-embed",
            model_revision="test-r1",
            embedding_dimension=dimension,
            normalization="L2",
            input_format="query.v1",
            distance_metric="COSINE",
            linkage_method=linkage_method.value,
            representative_method=representative_method.value,
            taxonomy_version="taxonomy.test.v1",
            baseline_policy_version=SUPPORTED_BASELINE_POLICY_VERSION,
            created_at=NOW,
        )
        session.add(policy)
        await session.flush()
        return policy.id


async def _report(
    *,
    session_digest: bytes,
    received_at: datetime,
    issue_type: str = "ORDER_SUBMISSION_FAILURE",
    submission_status: str = "CUSTOMER_REPORTED_NOT_SUBMITTED",
    error_code: str | None = "E100",
    symptom_text: str = "주문 버튼을 눌렀지만 접수되지 않았습니다.",
) -> UUID:
    async with session_factory() as session, session.begin():
        policy_snapshot = await session.scalar(select(PolicySnapshot).limit(1))
        if policy_snapshot is None:
            policy_snapshot = PolicySnapshot(
                version="signal-test-policy",
                source_url="https://example.invalid/policy",
                source_checked_on=received_at.date(),
                content={"schema_version": "test"},
                content_sha256="a" * 64,
                created_at=received_at,
            )
            session.add(policy_snapshot)
            await session.flush()
        report = Report(
            session_digest=session_digest,
            client_request_id=uuid4(),
            policy_snapshot_id=policy_snapshot.id,
            pii_policy_version="pii-mask.v1",
            masked_text=symptom_text,
            request_payload_sha256="b" * 64,
            status="CONFIRMED",
            received_at=received_at,
            purge_at=received_at + timedelta(hours=72),
            confirmed_at=received_at,
            updated_at=received_at,
        )
        technical = TechnicalSymptom(
            report=report,
            taxonomy_version="taxonomy.test.v1",
            channel="MABLE",
            feature_area="DOMESTIC_STOCK_ORDER",
            issue_type=issue_type,
            symptom=symptom_text,
            submission_status=submission_status,
            error_code=error_code,
            confirmed_at=received_at,
        )
        card = ConsultationCard(
            report=report,
            action="UNKNOWN",
            order_type="UNKNOWN",
            reference_digest=uuid4().bytes + uuid4().bytes,
            expires_at=received_at + timedelta(hours=2),
            confirmation_request_id=uuid4(),
            confirmation_payload_sha256="c" * 64,
            created_at=received_at,
            updated_at=received_at,
        )
        session.add_all((report, technical, card))
        await session.flush()
        session.add(
            enqueue_signal_processing(
                report_id=report.id,
                technical_symptom_id=technical.id,
                now=received_at,
            )
        )
        return report.id


async def _agent_token(role: AgentRole) -> str:
    token = make_opaque_token()
    settings = get_settings()
    async with session_factory() as session, session.begin():
        account = AgentAccount(
            employee_id=f"{role.value}99",
            agent_label=f"{role.value} test",
            role=role.value,
            password_hash=hash_password("not-used"),
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(account)
        await session.flush()
        session.add(
            AgentAccessToken(
                agent_id=account.id,
                token_digest=opaque_token_digest(
                    token,
                    settings.agent_token_hmac_key.get_secret_value().encode(),
                ),
                expires_at=NOW + timedelta(hours=72),
                created_at=NOW,
            )
        )
    return token


async def _standalone_cluster(*, status: SignalStatus) -> UUID:
    policy_id = await _policy(min_unique_sessions=1)
    async with session_factory() as session, session.begin():
        cluster = SignalCluster(
            policy_id=policy_id,
            status=status.value,
            channel="MABLE",
            feature_area="DOMESTIC_STOCK_ORDER",
            reported_symptom_type="ORDER_SUBMISSION_FAILURE",
            submission_status_filter="CUSTOMER_REPORTED_NOT_SUBMITTED",
            error_code_filter="E100",
            raw_report_count=1,
            reporting_unique_sessions=1,
            review_priority=False,
            first_report_at=NOW,
            last_report_at=NOW,
            official_incident=False,
            created_at=NOW,
            updated_at=NOW,
            purge_at=NOW + timedelta(hours=72),
        )
        session.add(cluster)
        await session.flush()
        return cluster.id


async def test_same_session_is_not_double_counted_and_candidate_is_hidden() -> None:
    await _policy(min_unique_sessions=2)
    provider = FakeEmbeddingProvider()
    first = await _report(session_digest=b"a" * 32, received_at=NOW)
    second = await _report(
        session_digest=b"a" * 32,
        received_at=NOW + timedelta(seconds=30),
    )

    async with session_factory() as session:
        assert await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        assert await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        dashboard = await list_dashboard_signals(
            session,
            now=NOW + timedelta(minutes=1),
            limit=50,
            offset=0,
        )
        assert dashboard.items == []
        cluster = await session.scalar(select(SignalCluster))
        assert cluster is not None
        assert cluster.status == SignalStatus.CANDIDATE.value
        assert cluster.raw_report_count == 2
        assert cluster.reporting_unique_sessions == 1
        member_reports = set(await session.scalars(select(SignalMember.report_id)))
        assert member_reports == {first, second}

    await _report(
        session_digest=b"c" * 32,
        received_at=NOW + timedelta(seconds=60),
    )
    async with session_factory() as session:
        assert await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=2))
        dashboard = await list_dashboard_signals(
            session,
            now=NOW + timedelta(minutes=2),
            limit=50,
            offset=0,
        )
        assert len(dashboard.items) == 1
        assert dashboard.items[0].status is SignalStatus.SIGNAL_DETECTED
        assert dashboard.items[0].reporting_unique_sessions == 2
        assert dashboard.items[0].baseline_status is BaselineStatus.INSUFFICIENT_HISTORY
        assert dashboard.items[0].baseline_ratio is None
        assert dashboard.items[0].official_incident is False
        assert dashboard.updated_at == NOW + timedelta(minutes=2)
        assert len(dashboard.hourly_volume) == 1
        assert dashboard.hourly_volume[0].raw_report_count == 3
        assert dashboard.hourly_volume[0].reporting_unique_sessions == 2
        assert dashboard.applied_policy is not None
        assert dashboard.applied_policy.window_seconds == 600
        assert dashboard.applied_policy.status is ClusteringPolicyStatus.EXPERIMENTAL


async def test_dashboard_hides_expired_detected_signal_but_keeps_reviewed_signal() -> None:
    policy_id = await _policy(min_unique_sessions=1)
    async with session_factory() as session, session.begin():
        session.add_all(
            (
                SignalCluster(
                    policy_id=policy_id,
                    status=SignalStatus.SIGNAL_DETECTED.value,
                    channel="MABLE",
                    feature_area="DOMESTIC_STOCK_ORDER",
                    reported_symptom_type="ORDER_SUBMISSION_FAILURE",
                    raw_report_count=1,
                    reporting_unique_sessions=1,
                    review_priority=False,
                    first_report_at=NOW,
                    last_report_at=NOW,
                    official_incident=False,
                    created_at=NOW,
                    updated_at=NOW,
                    purge_at=NOW + timedelta(hours=72),
                ),
                SignalCluster(
                    policy_id=policy_id,
                    status=SignalStatus.UNDER_REVIEW.value,
                    channel="MABLE",
                    feature_area="DOMESTIC_STOCK_ORDER",
                    reported_symptom_type="ORDER_SUBMISSION_FAILURE",
                    raw_report_count=1,
                    reporting_unique_sessions=1,
                    review_priority=False,
                    first_report_at=NOW,
                    last_report_at=NOW,
                    official_incident=False,
                    created_at=NOW,
                    updated_at=NOW,
                    purge_at=NOW + timedelta(hours=72),
                ),
            )
        )

    async with session_factory() as session:
        dashboard = await list_dashboard_signals(
            session,
            now=NOW + timedelta(seconds=601),
            limit=50,
            offset=0,
        )

    assert [item.status for item in dashboard.items] == [SignalStatus.UNDER_REVIEW]


async def test_related_signals_use_the_same_public_window_as_dashboard() -> None:
    await _policy(min_unique_sessions=1)
    report_id = await _report(session_digest=b"a" * 32, received_at=NOW)
    async with session_factory() as session:
        assert await process_next_signal_job(
            session,
            FakeEmbeddingProvider(),
            now=NOW + timedelta(minutes=1),
        )
        assert (
            await related_signals_for_report(
                session,
                report_id,
                now=NOW + timedelta(seconds=601),
            )
            == []
        )

    async with session_factory() as session, session.begin():
        cluster = await session.scalar(select(SignalCluster))
        assert cluster is not None
        cluster.status = SignalStatus.UNDER_REVIEW.value

    async with session_factory() as session:
        related = await related_signals_for_report(
            session,
            report_id,
            now=NOW + timedelta(seconds=601),
        )

    assert [signal.status for signal in related] == [SignalStatus.UNDER_REVIEW]


async def test_agent_card_distinguishes_candidate_signal_from_no_signal() -> None:
    await _policy(min_unique_sessions=2)
    report_id = await _report(session_digest=b"a" * 32, received_at=NOW)
    async with session_factory() as session:
        assert await process_next_signal_job(
            session,
            FakeEmbeddingProvider(),
            now=NOW + timedelta(minutes=1),
        )
        card_id = await session.scalar(
            select(ConsultationCard.id).where(ConsultationCard.report_id == report_id)
        )
        assert card_id is not None

    agent_token = await _agent_token(AgentRole.AGENT)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/consultation-cards/lookup",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"card_id": str(card_id)},
        )

    assert response.status_code == 200
    assert response.json()["related_signals"] == []
    assert response.json()["related_signal_state"] == "CANDIDATE"


async def test_structured_conflict_prevents_merge_even_with_same_embedding() -> None:
    await _policy(min_unique_sessions=2)
    provider = FakeEmbeddingProvider()
    await _report(session_digest=b"a" * 32, received_at=NOW, error_code="E100")
    await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=10),
        error_code="E200",
    )
    async with session_factory() as session:
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        assert await session.scalar(select(func.count()).select_from(SignalCluster)) == 2


@pytest.mark.parametrize(("similarity", "expected_clusters"), [(0.79, 1), (0.78, 2)])
async def test_similarity_policy_boundary(
    similarity: float,
    expected_clusters: int,
) -> None:
    await _policy(min_unique_sessions=1)
    await _report(session_digest=b"a" * 32, received_at=NOW)
    await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=1),
    )
    other_axis = math.sqrt(1 - similarity**2)
    provider = SequenceEmbeddingProvider([[1.0, 0.0, 0.0], [similarity, other_axis, 0.0]])
    async with session_factory() as session:
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        assert (
            await session.scalar(select(func.count()).select_from(SignalCluster))
            == expected_clusters
        )


async def test_average_linkage_prevents_single_member_chaining() -> None:
    await _policy(min_unique_sessions=1, similarity_threshold=0.7)
    for index in range(3):
        await _report(
            session_digest=bytes([index + 1]) * 32,
            received_at=NOW + timedelta(seconds=index),
        )
    angle_40 = math.radians(40)
    angle_80 = math.radians(80)
    provider = SequenceEmbeddingProvider(
        [
            [1.0, 0.0, 0.0],
            [math.cos(angle_40), math.sin(angle_40), 0.0],
            [math.cos(angle_80), math.sin(angle_80), 0.0],
        ]
    )

    async with session_factory() as session:
        for _ in range(3):
            await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        member_counts = list(
            await session.scalars(
                select(func.count(SignalMember.id))
                .group_by(SignalMember.signal_id)
                .order_by(func.count(SignalMember.id).desc())
            )
        )

    assert member_counts == [2, 1]


async def test_average_linkage_reconciles_existing_multi_member_clusters() -> None:
    await _policy(min_unique_sessions=5, similarity_threshold=0.58)
    report_ids = []
    for index in range(5):
        report_ids.append(
            await _report(
                session_digest=bytes([index + 1]) * 32,
                received_at=NOW + timedelta(seconds=index),
            )
        )
    angle_30 = math.radians(30)
    angle_60 = math.radians(60)
    provider = SequenceEmbeddingProvider(
        [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [math.cos(angle_60), math.sin(angle_60), 0.0],
            [math.cos(angle_60), math.sin(angle_60), 0.0],
            [math.cos(angle_30), math.sin(angle_30), 0.0],
        ]
    )

    async with session_factory() as session:
        for _ in range(5):
            await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        active_cluster = await session.scalar(
            select(SignalCluster).where(SignalCluster.status == SignalStatus.SIGNAL_DETECTED.value)
        )
        assert active_cluster is not None
        assert active_cluster.raw_report_count == 5
        assert active_cluster.reporting_unique_sessions == 5
        bridge_symptom_id = await session.scalar(
            select(TechnicalSymptom.id).where(TechnicalSymptom.report_id == report_ids[-1])
        )
        assert active_cluster.representative_symptom_id == bridge_symptom_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SignalCluster)
                .where(SignalCluster.status == SignalStatus.CLOSED.value)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SignalAuditEvent)
                .where(SignalAuditEvent.action == "SIGNAL_AUTOMATICALLY_MERGED")
            )
            == 1
        )


async def test_medoid_is_recalculated_after_each_membership_change() -> None:
    await _policy(min_unique_sessions=1, similarity_threshold=0.45)
    report_ids = []
    for index in range(3):
        report_ids.append(
            await _report(
                session_digest=bytes([index + 1]) * 32,
                received_at=NOW + timedelta(seconds=index),
            )
        )
    angle_30 = math.radians(30)
    angle_60 = math.radians(60)
    provider = SequenceEmbeddingProvider(
        [
            [1.0, 0.0, 0.0],
            [math.cos(angle_30), math.sin(angle_30), 0.0],
            [math.cos(angle_60), math.sin(angle_60), 0.0],
        ]
    )

    async with session_factory() as session:
        for _ in range(3):
            await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        cluster = await session.scalar(select(SignalCluster))
        middle_symptom_id = await session.scalar(
            select(TechnicalSymptom.id).where(TechnicalSymptom.report_id == report_ids[1])
        )
        assert cluster is not None
        assert cluster.representative_symptom_id == middle_symptom_id


async def test_agent_signal_verification_lock_flow_is_idempotent_and_conflict_safe() -> None:
    await _policy(min_unique_sessions=1, similarity_threshold=0.58)
    report_id = await _report(session_digest=b"v" * 32, received_at=NOW)
    provider = FakeEmbeddingProvider()
    async with session_factory() as session:
        processed = await process_next_signal_job(
            session,
            provider,
            now=NOW + timedelta(minutes=1),
        )
        assert processed is not None
        assert processed.signal_id is not None
        signal_id = processed.signal_id
        card_id = await session.scalar(
            select(ConsultationCard.id).where(ConsultationCard.report_id == report_id)
        )
        assert card_id is not None

    token = await _agent_token(AgentRole.AGENT)
    transport = ASGITransport(app=app)

    async def verify(decision: str, request_id: str) -> Response:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/consultation-cards/signal-verifications",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "card_id": str(card_id),
                    "signal_id": str(signal_id),
                    "decision": decision,
                    "client_request_id": request_id,
                },
            )

    blocked = await verify("UNCONFIRMED", str(uuid4()))
    assert blocked.status_code == 200
    assert blocked.json()["lock_decision"] == "BLOCK"

    allow_id = str(uuid4())
    allowed = await verify("RELATED", allow_id)
    replay = await verify("RELATED", allow_id)
    idempotent = await verify("RELATED", str(uuid4()))
    conflict = await verify("NOT_RELATED", str(uuid4()))

    assert allowed.status_code == replay.status_code == idempotent.status_code == 200
    assert allowed.json()["lock_decision"] == replay.json()["lock_decision"] == "ALLOW"
    assert idempotent.json()["lock_decision"] == "IDEMPOTENT_REPLAY"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "SIGNAL_RELEVANCE_CONFLICT"

    async with session_factory() as session:
        lock = await session.scalar(select(SignalRelevanceLock))
        assert lock is not None
        assert lock.final_related is True
        assert await session.scalar(select(func.count()).select_from(SignalRelevanceLock)) == 1
        assert await session.scalar(select(func.count()).select_from(AgentSignalVerification)) == 4
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "AGENT_SIGNAL_CONFLICT")
            )
            == 1
        )


async def test_1024_dimension_ann_expression_is_usable() -> None:
    await _policy(min_unique_sessions=1, dimension=1024)
    await _report(session_digest=b"a" * 32, received_at=NOW)
    await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=1),
    )
    first = [1.0, *([0.0] * 1023)]
    second = [0.8, 0.6, *([0.0] * 1022)]
    provider = SequenceEmbeddingProvider([first, second])

    async with session_factory() as session:
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        assert await session.scalar(select(func.count()).select_from(SignalCluster)) == 1

        await session.execute(text("SET LOCAL enable_seqscan = off"))
        vector_literal = "[" + ",".join(str(value) for value in first) + "]"
        plan_rows = await session.execute(
            text(
                "EXPLAIN SELECT id FROM technical_embeddings "
                "WHERE embedding_dimension = 1024 "
                "AND normalization = 'L2' AND distance_metric = 'COSINE' "
                "ORDER BY embedding::vector(1024) <=> CAST(:vector AS vector(1024)) LIMIT 1"
            ),
            {"vector": vector_literal},
        )
        plan = "\n".join(str(row[0]) for row in plan_rows)
        assert "ix_technical_embeddings_1024_hnsw_cosine" in plan


@pytest.mark.parametrize(("offset_seconds", "expected_clusters"), [(600, 1), (601, 2)])
async def test_rolling_window_boundary(
    offset_seconds: int,
    expected_clusters: int,
) -> None:
    await _policy(min_unique_sessions=1)
    await _report(session_digest=b"a" * 32, received_at=NOW)
    await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=offset_seconds),
    )
    provider = FakeEmbeddingProvider()
    async with session_factory() as session:
        await process_next_signal_job(
            session,
            provider,
            now=NOW + timedelta(minutes=20),
        )
        await process_next_signal_job(
            session,
            provider,
            now=NOW + timedelta(minutes=20),
        )
        assert (
            await session.scalar(select(func.count()).select_from(SignalCluster))
            == expected_clusters
        )


async def test_rolling_counts_exclude_old_members_from_signal_threshold() -> None:
    await _policy(min_unique_sessions=3)
    for index, offset in enumerate((0, 600, 1200), start=1):
        await _report(
            session_digest=bytes([index]) * 32,
            received_at=NOW + timedelta(seconds=offset),
        )
    provider = FakeEmbeddingProvider()

    async with session_factory() as session:
        for _ in range(3):
            await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=21))
        cluster = await session.scalar(select(SignalCluster))
        assert cluster is not None
        assert cluster.status == SignalStatus.CANDIDATE.value
        assert cluster.raw_report_count == 2
        assert cluster.reporting_unique_sessions == 2
        assert await session.scalar(select(func.count()).select_from(SignalMember)) == 3


async def test_previous_window_baseline_becomes_available() -> None:
    await _policy(min_unique_sessions=1)
    for index, offset in enumerate((0, 600, 1200), start=1):
        await _report(
            session_digest=bytes([index]) * 32,
            received_at=NOW + timedelta(seconds=offset),
        )
    provider = FakeEmbeddingProvider()

    async with session_factory() as session:
        for _ in range(3):
            await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=21))
        dashboard = await list_dashboard_signals(
            session,
            now=NOW + timedelta(minutes=20),
            limit=50,
            offset=0,
        )

    assert len(dashboard.items) == 1
    assert dashboard.items[0].raw_report_count == 2
    assert dashboard.items[0].reporting_unique_sessions == 2
    assert dashboard.items[0].baseline_status is BaselineStatus.AVAILABLE
    assert dashboard.items[0].baseline_ratio == 2.0
    assert dashboard.baseline_status is BaselineStatus.AVAILABLE
    assert dashboard.baseline_ratio == 2.0
    assert sum(bucket.raw_report_count for bucket in dashboard.hourly_volume) == 2


async def test_previous_window_baseline_distinguishes_zero_history() -> None:
    await _policy(min_unique_sessions=1)
    await _report(
        session_digest=b"z" * 32,
        received_at=NOW + timedelta(seconds=1200),
    )

    async with session_factory() as session:
        await process_next_signal_job(
            session,
            FakeEmbeddingProvider(),
            now=NOW + timedelta(minutes=21),
        )
        dashboard = await list_dashboard_signals(
            session,
            now=NOW + timedelta(minutes=20),
            limit=50,
            offset=0,
        )

    assert dashboard.items[0].baseline_status is BaselineStatus.ZERO_BASELINE
    assert dashboard.items[0].baseline_ratio is None
    assert dashboard.baseline_status is BaselineStatus.ZERO_BASELINE
    assert dashboard.baseline_ratio is None


async def test_policy_metadata_mismatch_fails_without_deleting_report_or_card() -> None:
    await _policy()
    report_id = await _report(session_digest=b"a" * 32, received_at=NOW)
    provider = FakeEmbeddingProvider(model_id="unexpected-model")

    async with session_factory() as session:
        result = await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        assert result is not None
        assert result.status is SignalProcessingStatus.DEAD_LETTER
        assert result.safe_error_code == "POLICY_MISMATCH"
        assert await session.get(Report, report_id) is not None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ConsultationCard)
                .where(ConsultationCard.report_id == report_id)
            )
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(SignalCluster)) == 0


async def test_worker_preflight_leaves_jobs_pending_on_runtime_policy_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _policy()
    await _report(session_digest=b"a" * 32, received_at=NOW)
    monkeypatch.setattr(
        process_signal_jobs_script,
        "load_signal_embedding_contract",
        lambda: SignalEmbeddingContract(
            model_id="fake-embed",
            model_revision="different-revision",
            dimension=3,
            normalization="L2",
            input_format="query.v1",
            distance_metric="COSINE",
        ),
    )
    monkeypatch.setattr(
        process_signal_jobs_script,
        "OpenAiSignalEmbeddingAdapter",
        lambda: pytest.fail("provider must not be initialized"),
    )

    with pytest.raises(RuntimeError, match="model_revision"):
        await process_signal_jobs_script.run(max_jobs=1)

    async with session_factory() as session:
        job = await session.scalar(select(SignalProcessingJob))
        assert job is not None
        assert job.status == SignalProcessingStatus.PENDING.value
        assert job.attempt_count == 0


async def test_runtime_readiness_requires_policy_symbols_and_both_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures = await check_runtime_readiness.collect_failures()
    assert failures == (
        "ACTIVE_SIGNAL_POLICY_MISSING",
        "ACTIVE_SYMBOL_MASTER_MISSING",
        "ACTIVE_AGENT_MISSING",
        "ACTIVE_OPERATOR_MISSING",
    )

    await _policy()
    monkeypatch.setattr(
        readiness,
        "load_signal_embedding_contract",
        lambda: SignalEmbeddingContract(
            model_id="fake-embed",
            model_revision="test-r1",
            dimension=3,
            normalization="L2",
            input_format="query.v1",
            distance_metric="COSINE",
        ),
    )
    async with session_factory() as session, session.begin():
        session.add(
            SymbolMasterVersion(
                version="krx-test-ready",
                source_url="https://example.invalid/krx.csv",
                source_as_of=NOW.date(),
                source_sha256="d" * 64,
                source_encoding="UTF-8-SIG",
                schema_version="krx-symbol-master.v1",
                row_count=1,
                is_active=True,
                imported_at=NOW,
            )
        )
        for role in AgentRole:
            session.add(
                AgentAccount(
                    employee_id=f"READY-{role.value}",
                    agent_label=f"{role.value} ready",
                    role=role.value,
                    password_hash=hash_password("not-used"),
                    is_active=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

    assert await check_runtime_readiness.collect_failures() == ()


@pytest.mark.parametrize(
    ("provider", "expected_code", "expected_status"),
    [
        (
            InvalidEmbeddingProvider(),
            "INVALID_EMBEDDING",
            SignalProcessingStatus.DEAD_LETTER,
        ),
        (
            FailingEmbeddingProvider(TimeoutError()),
            "EMBEDDING_UNAVAILABLE",
            SignalProcessingStatus.FAILED,
        ),
        (
            FailingEmbeddingProvider(OSError()),
            "EMBEDDING_UNAVAILABLE",
            SignalProcessingStatus.FAILED,
        ),
    ],
)
async def test_embedding_failures_are_safe_and_retryable(
    provider: InvalidEmbeddingProvider | FailingEmbeddingProvider,
    expected_code: str,
    expected_status: SignalProcessingStatus,
) -> None:
    await _policy()
    report_id = await _report(session_digest=b"a" * 32, received_at=NOW)
    async with session_factory() as session:
        result = await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        assert result is not None
        assert result.status is expected_status
        assert result.safe_error_code == expected_code
        assert await session.get(Report, report_id) is not None
        job = await session.scalar(
            select(SignalProcessingJob).where(SignalProcessingJob.report_id == report_id)
        )
        assert job is not None
        assert job.next_attempt_at == (
            NOW + timedelta(minutes=6)
            if expected_status is SignalProcessingStatus.FAILED
            else NOW + timedelta(minutes=1)
        )


async def test_worker_dead_letters_retryable_failure_after_max_attempts() -> None:
    await _policy()
    await _report(session_digest=b"a" * 32, received_at=NOW)
    provider = FailingEmbeddingProvider(TimeoutError())

    async with session_factory() as session:
        first = await process_next_signal_job(
            session,
            provider,
            now=NOW + timedelta(minutes=1),
            max_attempts=2,
        )
        second = await process_next_signal_job(
            session,
            provider,
            now=NOW + timedelta(minutes=6),
            max_attempts=2,
        )
        exhausted = await process_next_signal_job(
            session,
            provider,
            now=NOW + timedelta(minutes=11),
            max_attempts=2,
        )
        job = await session.scalar(select(SignalProcessingJob))

    assert first is not None and first.status is SignalProcessingStatus.FAILED
    assert second is not None and second.status is SignalProcessingStatus.DEAD_LETTER
    assert second.safe_error_code == "RETRY_EXHAUSTED"
    assert exhausted is None
    assert job is not None and job.attempt_count == 2


async def test_worker_cli_returns_nonzero_when_a_job_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _policy()
    await _report(session_digest=b"a" * 32, received_at=NOW)
    monkeypatch.setattr(
        process_signal_jobs_script,
        "load_signal_embedding_contract",
        lambda: SignalEmbeddingContract(
            model_id="fake-embed",
            model_revision="test-r1",
            dimension=3,
            normalization="L2",
            input_format="query.v1",
            distance_metric="COSINE",
        ),
    )
    monkeypatch.setattr(
        process_signal_jobs_script,
        "OpenAiSignalEmbeddingAdapter",
        lambda: FailingEmbeddingProvider(TimeoutError()),
    )
    monkeypatch.setattr(
        process_signal_jobs_script,
        "utc_now",
        lambda: NOW + timedelta(minutes=1),
    )

    assert await process_signal_jobs_script.run(max_jobs=1) == 1


async def test_concurrent_workers_create_one_cluster() -> None:
    await _policy(min_unique_sessions=2)
    await _report(session_digest=b"a" * 32, received_at=NOW)
    await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=1),
    )
    provider = FakeEmbeddingProvider()

    async def process_one() -> None:
        async with session_factory() as session:
            result = await process_next_signal_job(
                session,
                provider,
                now=NOW + timedelta(minutes=1),
            )
            assert result is not None

    await asyncio.gather(process_one(), process_one())
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SignalCluster)) == 1
        assert await session.scalar(select(func.count()).select_from(SignalMember)) == 2
        cluster = await session.scalar(select(SignalCluster))
        assert cluster is not None
        assert cluster.status == SignalStatus.SIGNAL_DETECTED.value


async def test_report_deletion_recalculates_and_closes_signal() -> None:
    await _policy(min_unique_sessions=2)
    first = await _report(session_digest=b"a" * 32, received_at=NOW)
    second = await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=1),
    )
    provider = FakeEmbeddingProvider()
    async with session_factory() as session:
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        async with session.begin():
            await detach_report_from_signals(
                session,
                second,
                now=NOW + timedelta(minutes=2),
            )
            await session.execute(delete(Report).where(Report.id == second))
        cluster = await session.scalar(select(SignalCluster))
        assert cluster is not None
        assert cluster.status == SignalStatus.CLOSED.value
        assert cluster.closure_reason == SignalClosureReason.EVIDENCE_RECALCULATED.value
        assert cluster.raw_report_count == 1
        assert cluster.reporting_unique_sessions == 1
        assert cluster.representative_symptom_id is None
        assert await session.get(Report, first) is not None
        assert await session.get(Report, second) is None


async def test_dashboard_auth_and_operator_role_idempotency() -> None:
    signal_id = await _standalone_cluster(status=SignalStatus.SIGNAL_DETECTED)
    operator_token = await _agent_token(AgentRole.OPERATOR)
    agent_token = await _agent_token(AgentRole.AGENT)
    request_id = str(uuid4())
    payload = {
        "signal_id": str(signal_id),
        "reason": "MANUAL_REVIEW",
        "client_request_id": request_id,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthenticated = await client.get("/api/signals/dashboard")
        customer_dashboard = await client.get(
            "/api/signals/dashboard",
            headers={"Authorization": f"Bearer {make_opaque_token()}"},
        )
        wrong_role = await client.post(
            "/api/operator/signals/acknowledge",
            headers={"Authorization": f"Bearer {agent_token}"},
            json=payload,
        )
        acknowledged = await client.post(
            "/api/operator/signals/acknowledge",
            headers={"Authorization": f"Bearer {operator_token}"},
            json=payload,
        )
        replay = await client.post(
            "/api/operator/signals/acknowledge",
            headers={"Authorization": f"Bearer {operator_token}"},
            json=payload,
        )
        conflict = await client.post(
            "/api/operator/signals/acknowledge",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={**payload, "reason": "DIFFERENT_REASON"},
        )
        notice = await client.post(
            "/api/operator/signals/official-notice",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={
                "signal_id": str(signal_id),
                "official_notice_url": "https://status.example.invalid/incidents/1",
                "client_request_id": str(uuid4()),
            },
        )
        closed = await client.post(
            "/api/operator/signals/close",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={
                "signal_id": str(signal_id),
                "closure_reason": "FALSE_POSITIVE",
                "client_request_id": str(uuid4()),
            },
        )
        closed_dashboard = await client.get(
            "/api/signals/dashboard",
            headers={"Authorization": f"Bearer {make_opaque_token()}"},
        )

    assert unauthenticated.status_code == 401
    assert customer_dashboard.status_code == 200
    assert customer_dashboard.headers["cache-control"] == "no-store"
    assert len(customer_dashboard.json()["items"]) == 1
    assert wrong_role.status_code == 403
    assert acknowledged.status_code == replay.status_code == 200
    assert acknowledged.json()["status"] == "UNDER_REVIEW"
    assert acknowledged.headers["cache-control"] == "no-store"
    assert conflict.status_code == 409
    assert notice.status_code == 200
    assert notice.json()["official_notice_url"].startswith("https://")
    assert notice.json()["status"] == "UNDER_REVIEW"
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["closure_reason"] == "FALSE_POSITIVE"
    assert closed_dashboard.json()["items"] == []
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SignalAuditEvent)
                .where(SignalAuditEvent.action == "SIGNAL_ACKNOWLEDGED")
            )
            == 1
        )


async def test_operator_approves_signal_policy_with_evaluation_evidence_once() -> None:
    policy_id = await _policy()
    operator_token = await _agent_token(AgentRole.OPERATOR)
    agent_token = await _agent_token(AgentRole.AGENT)
    async with session_factory() as session:
        policy_version = await session.scalar(
            select(ClusteringPolicy.policy_version).where(ClusteringPolicy.id == policy_id)
        )
    assert policy_version is not None
    request_id = str(uuid4())
    payload = {
        "policy_version": policy_version,
        "evaluation_artifact_sha256": "e" * 64,
        "client_request_id": request_id,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        wrong_role = await client.post(
            "/api/operator/signal-policies/approve",
            headers={"Authorization": f"Bearer {agent_token}"},
            json=payload,
        )
        approved = await client.post(
            "/api/operator/signal-policies/approve",
            headers={"Authorization": f"Bearer {operator_token}"},
            json=payload,
        )
        replay = await client.post(
            "/api/operator/signal-policies/approve",
            headers={"Authorization": f"Bearer {operator_token}"},
            json=payload,
        )
        conflict = await client.post(
            "/api/operator/signal-policies/approve",
            headers={"Authorization": f"Bearer {operator_token}"},
            json={**payload, "evaluation_artifact_sha256": "f" * 64},
        )
        metrics = await client.get(
            "/api/operator/operations/metrics",
            headers={"Authorization": f"Bearer {operator_token}"},
        )

    assert wrong_role.status_code == 403
    assert approved.status_code == replay.status_code == 200
    assert approved.json() == replay.json()
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["evaluation_artifact_sha256"] == "e" * 64
    assert approved.headers["cache-control"] == "no-store"
    assert conflict.status_code == 409
    assert metrics.status_code == 200
    assert metrics.headers["cache-control"] == "no-store"
    assert metrics.json()["signal_jobs_dead_letter"] == 0
    async with session_factory() as session:
        policy = await session.get(ClusteringPolicy, policy_id)
        assert policy is not None
        assert policy.status == ClusteringPolicyStatus.APPROVED.value
        assert policy.approval_evidence_sha256 == "e" * 64
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "SIGNAL_POLICY_APPROVED")
            )
            == 1
        )


async def test_operator_can_list_window_expired_detected_signals() -> None:
    signal_id = await _standalone_cluster(status=SignalStatus.SIGNAL_DETECTED)
    operator_token = await _agent_token(AgentRole.OPERATOR)
    agent_token = await _agent_token(AgentRole.AGENT)
    app.dependency_overrides[get_clock] = lambda: lambda: NOW + timedelta(seconds=601)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthenticated = await client.get("/api/operator/signals")
        wrong_role = await client.get(
            "/api/operator/signals",
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        listed = await client.get(
            "/api/operator/signals?status=SIGNAL_DETECTED",
            headers={"Authorization": f"Bearer {operator_token}"},
        )

    assert unauthenticated.status_code == 401
    assert wrong_role.status_code == 403
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()["items"] == [
        {
            "signal_id": str(signal_id),
            "status": "SIGNAL_DETECTED",
            "closure_reason": None,
            "channel": "MABLE",
            "feature_area": "DOMESTIC_STOCK_ORDER",
            "reported_symptom_type": "ORDER_SUBMISSION_FAILURE",
            "representative_symptom_text": None,
            "reporting_unique_sessions": 1,
            "raw_report_count": 1,
            "review_priority": False,
            "first_report_at": NOW.isoformat().replace("+00:00", "Z"),
            "last_report_at": NOW.isoformat().replace("+00:00", "Z"),
            "window_expires_at": (NOW + timedelta(seconds=600)).isoformat().replace("+00:00", "Z"),
            "public_visible": False,
            "policy_version": listed.json()["items"][0]["policy_version"],
            "policy_status": "EXPERIMENTAL",
            "official_notice_url": None,
            "closed_at": None,
        }
    ]


async def test_dashboard_rate_limit_is_atomic() -> None:
    token = make_opaque_token()
    settings = Settings(signal_dashboard_limit=2, signal_dashboard_window_seconds=60)
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(
                *(
                    client.get(
                        "/api/signals/dashboard",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    for _ in range(5)
                )
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    statuses = [response.status_code for response in responses]
    assert statuses.count(200) == 2
    assert statuses.count(429) == 3
    assert {
        response.headers["retry-after"] for response in responses if response.status_code == 429
    } == {"60"}


async def test_retention_purge_removes_signal_evidence_and_metadata(tmp_path: Path) -> None:
    await _policy(min_unique_sessions=2)
    received_at = NOW - timedelta(hours=73)
    await _report(session_digest=b"a" * 32, received_at=received_at)
    await _report(
        session_digest=b"b" * 32,
        received_at=received_at + timedelta(seconds=1),
    )
    provider = FakeEmbeddingProvider()
    async with session_factory() as session:
        await process_next_signal_job(
            session,
            provider,
            now=received_at + timedelta(minutes=1),
        )
        await process_next_signal_job(
            session,
            provider,
            now=received_at + timedelta(minutes=1),
        )
        result = await purge_expired_data(
            session,
            LocalAttachmentStore(tmp_path / "attachments"),
            now=NOW,
            batch_size=1,
        )
        assert result.reports_deleted == 2
        assert result.signal_clusters_deleted == 1
        assert result.signal_audit_events_deleted >= 1
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
        assert await session.scalar(select(func.count()).select_from(TechnicalEmbedding)) == 0
        assert await session.scalar(select(func.count()).select_from(SignalMember)) == 0
        assert await session.scalar(select(func.count()).select_from(SignalCluster)) == 0


async def test_operator_merge_and_split_are_transactional_and_audited() -> None:
    await _policy(min_unique_sessions=1)
    first_report = await _report(session_digest=b"a" * 32, received_at=NOW)
    second_report = await _report(
        session_digest=b"b" * 32,
        received_at=NOW + timedelta(seconds=1),
    )
    provider = SequenceEmbeddingProvider([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    operator_token = await _agent_token(AgentRole.OPERATOR)
    async with session_factory() as session:
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
        await process_next_signal_job(session, provider, now=NOW + timedelta(minutes=1))
    async with session_factory() as session:
        clusters = list(
            (await session.scalars(select(SignalCluster).order_by(SignalCluster.id))).all()
        )
        assert len(clusters) == 2
        source, target = clusters
        source_id = source.id
        target_id = target.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {operator_token}"}
        merged = await client.post(
            "/api/operator/signals/merge",
            headers=headers,
            json={
                "source_signal_id": str(source_id),
                "target_signal_id": str(target_id),
                "reason": "MANUAL_REVIEW",
                "client_request_id": str(uuid4()),
            },
        )
        assert merged.status_code == 200
        assert merged.headers["cache-control"] == "no-store"
        assert merged.json()["signal_id"] == str(target_id)
        assert merged.json()["raw_report_count"] == 2

        split = await client.post(
            "/api/operator/signals/split",
            headers=headers,
            json={
                "signal_id": str(target_id),
                "report_ids": [str(first_report)],
                "reason": "FALSE_GROUPING",
                "client_request_id": str(uuid4()),
            },
        )
        assert split.status_code == 200
        assert split.headers["cache-control"] == "no-store"
        assert split.json()["signal_id"] == str(target_id)

    async with session_factory() as session:
        member_counts = list(
            (
                await session.execute(
                    select(SignalMember.signal_id, func.count(SignalMember.id))
                    .group_by(SignalMember.signal_id)
                    .order_by(SignalMember.signal_id)
                )
            ).all()
        )
        assert sorted(count for _, count in member_counts) == [1, 1]
        assert {
            action
            for action in await session.scalars(
                select(SignalAuditEvent.action).where(
                    SignalAuditEvent.action.in_(("SIGNAL_MERGED", "SIGNAL_SPLIT"))
                )
            )
        } == {"SIGNAL_MERGED", "SIGNAL_SPLIT"}
        assert await session.get(Report, second_report) is not None
