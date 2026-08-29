import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import ServiceError
from app.security import InvalidSessionTokenError, session_digest
from app.services.agents import AgentPrincipal, Sleeper, resolve_agent_token
from app.services.lifecycle import utc_now

bearer = HTTPBearer(auto_error=False)


def get_clock() -> Callable[[], datetime]:
    return utc_now


def get_security_sleeper() -> Sleeper:
    return asyncio.sleep


def customer_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> bytes:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ServiceError(401, "AUTH_REQUIRED", "인증 정보가 필요합니다.")
    try:
        return session_digest(
            credentials.credentials,
            settings.session_hmac_key.get_secret_value().encode(),
        )
    except (InvalidSessionTokenError, ValueError) as exc:
        raise ServiceError(401, "INVALID_SESSION", "인증 정보가 올바르지 않습니다.") from exc


async def agent_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AgentPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ServiceError(401, "AUTH_REQUIRED", "인증 정보가 필요합니다.")
    async with session.begin():
        principal = await resolve_agent_token(
            session,
            credentials.credentials,
            settings,
            now=clock(),
        )
    if principal.role.value != "AGENT":
        raise ServiceError(403, "AGENT_ROLE_REQUIRED", "상담원 권한이 필요합니다.")
    return principal
