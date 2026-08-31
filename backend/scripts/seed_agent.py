import argparse
import asyncio
import os
import unicodedata

from sqlalchemy import select

from app.codes import AgentRole
from app.config import get_settings
from app.db import engine, session_factory
from app.models import AgentAccount
from app.security import hash_password


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision one agent account")
    parser.add_argument("--employee-id", default="CS1024")
    parser.add_argument("--agent-label", default="CS1024 상담원")
    parser.add_argument("--role", choices=[role.value for role in AgentRole], default="AGENT")
    parser.add_argument(
        "--password",
        help="development fallback only; AGENT_INITIAL_PASSWORD takes precedence",
    )
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    settings = get_settings()
    password = os.getenv("AGENT_INITIAL_PASSWORD")
    if not password and settings.app_env != "production":
        password = os.getenv("DEMO_AGENT_PASSWORD") or arguments.password
    if not password:
        raise SystemExit("AGENT_INITIAL_PASSWORD must be set")
    employee_id = unicodedata.normalize("NFC", arguments.employee_id.strip()).upper()
    agent_label = unicodedata.normalize("NFC", arguments.agent_label.strip())
    if not employee_id or not agent_label:
        raise SystemExit("employee ID and agent label must not be blank")

    try:
        async with session_factory() as session, session.begin():
            account = await session.scalar(
                select(AgentAccount)
                .where(AgentAccount.employee_id == employee_id)
                .with_for_update()
            )
            created = account is None
            if account is None:
                account = AgentAccount(employee_id=employee_id)
                session.add(account)
            account.agent_label = agent_label
            account.role = arguments.role
            account.password_hash = hash_password(password)
            account.is_active = True
        print(f"agent={'created' if created else 'updated'} role={arguments.role}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
