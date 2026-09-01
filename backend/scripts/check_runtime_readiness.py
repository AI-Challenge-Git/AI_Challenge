import asyncio

from sqlalchemy import func, select

from app.codes import AgentRole
from app.db import engine, session_factory
from app.models import AgentAccount, ClusteringPolicy, SymbolMasterVersion
from app.services.signal_embeddings import (
    embedding_contract_mismatches,
    load_signal_embedding_contract,
)


async def collect_failures() -> tuple[str, ...]:
    failures: list[str] = []
    async with session_factory() as session:
        policy = await session.scalar(
            select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
        )
        if policy is None:
            failures.append("ACTIVE_SIGNAL_POLICY_MISSING")
        else:
            try:
                contract = load_signal_embedding_contract()
            except RuntimeError:
                failures.append("SIGNAL_EMBEDDING_CONTRACT_MISSING")
            else:
                if embedding_contract_mismatches(
                    contract,
                    model_id=policy.model_id,
                    model_revision=policy.model_revision,
                    dimension=policy.embedding_dimension,
                    normalization=policy.normalization,
                    input_format=policy.input_format,
                    distance_metric=policy.distance_metric,
                ):
                    failures.append("SIGNAL_POLICY_CONTRACT_MISMATCH")

        symbol_master = await session.scalar(
            select(SymbolMasterVersion).where(SymbolMasterVersion.is_active.is_(True))
        )
        if symbol_master is None or symbol_master.row_count < 1:
            failures.append("ACTIVE_SYMBOL_MASTER_MISSING")

        role_rows = (
            await session.execute(
                select(AgentAccount.role, func.count(AgentAccount.id))
                .where(AgentAccount.is_active.is_(True))
                .group_by(AgentAccount.role)
            )
        ).all()
        role_counts: dict[str, int] = {role: int(count) for role, count in role_rows}
        if int(role_counts.get(AgentRole.AGENT.value, 0)) < 1:
            failures.append("ACTIVE_AGENT_MISSING")
        if int(role_counts.get(AgentRole.OPERATOR.value, 0)) < 1:
            failures.append("ACTIVE_OPERATOR_MISSING")
    return tuple(failures)


async def run() -> int:
    try:
        failures = await collect_failures()
        if failures:
            print(f"runtime_ready=false failures={','.join(failures)}")
            return 1
        print("runtime_ready=true")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
