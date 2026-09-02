import asyncio
import json

from sqlalchemy import func, select

from app.codes import AgentRole
from app.config import get_settings
from app.db import engine, session_factory
from app.models import AgentAccount
from app.services.readiness import collect_service_readiness_failures


async def collect_failures() -> tuple[str, ...]:
    failures: list[str]
    async with session_factory() as session:
        failures = list(await collect_service_readiness_failures(session, get_settings()))

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
        ready = not failures
        print(
            json.dumps(
                {"event": "runtime_readiness", "ready": ready, "failures": failures},
                separators=(",", ":"),
            )
        )
        return 0 if ready else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
