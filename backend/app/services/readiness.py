from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import OpenAIDualExtractorAdapter
from app.config import Settings
from app.models import ClusteringPolicy, PolicySnapshot, SymbolMasterVersion
from app.services.policies import InvalidPolicySnapshotError, consultation_safety_notice
from app.services.signal_embeddings import (
    embedding_contract_mismatches,
    load_signal_embedding_contract,
)


async def collect_service_readiness_failures(
    session: AsyncSession,
    settings: Settings,
) -> tuple[str, ...]:
    await session.execute(text("SELECT 1"))
    failures: list[str] = []

    policy_snapshot = await session.scalar(
        select(PolicySnapshot).where(PolicySnapshot.version == settings.active_policy_version)
    )
    if policy_snapshot is None:
        failures.append("ACTIVE_GUIDANCE_POLICY_MISSING")
    else:
        try:
            consultation_safety_notice(policy_snapshot)
        except InvalidPolicySnapshotError:
            failures.append("ACTIVE_GUIDANCE_POLICY_INVALID")

    signal_policy = await session.scalar(
        select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
    )
    if signal_policy is None:
        failures.append("ACTIVE_SIGNAL_POLICY_MISSING")
    else:
        if signal_policy.taxonomy_version != OpenAIDualExtractorAdapter.taxonomy_version:
            failures.append("SIGNAL_POLICY_TAXONOMY_MISMATCH")
        try:
            contract = load_signal_embedding_contract()
        except RuntimeError:
            failures.append("SIGNAL_EMBEDDING_CONTRACT_MISSING")
        else:
            if embedding_contract_mismatches(
                contract,
                model_id=signal_policy.model_id,
                model_revision=signal_policy.model_revision,
                dimension=signal_policy.embedding_dimension,
                normalization=signal_policy.normalization,
                input_format=signal_policy.input_format,
                distance_metric=signal_policy.distance_metric,
            ):
                failures.append("SIGNAL_POLICY_CONTRACT_MISMATCH")

    symbol_master = await session.scalar(
        select(SymbolMasterVersion).where(SymbolMasterVersion.is_active.is_(True))
    )
    if symbol_master is None or symbol_master.row_count < 1:
        failures.append("ACTIVE_SYMBOL_MASTER_MISSING")
    return tuple(failures)
