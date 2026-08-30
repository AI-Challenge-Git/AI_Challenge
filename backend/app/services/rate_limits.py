import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.codes import RateLimitScope
from app.errors import ServiceError
from app.models import RateLimitBucket


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    count: int
    expires_at: datetime


async def consume_rate_limit(
    session: AsyncSession,
    *,
    scope: RateLimitScope,
    principal_fingerprint: bytes,
    client_fingerprint: bytes,
    now: datetime,
    window_seconds: int,
) -> RateLimitResult:
    expires_at = now + timedelta(seconds=window_seconds)
    expired = RateLimitBucket.expires_at <= now
    statement = (
        insert(RateLimitBucket)
        .values(
            scope=scope.value,
            principal_fingerprint=principal_fingerprint,
            client_fingerprint=client_fingerprint,
            window_started_at=now,
            request_count=1,
            expires_at=expires_at,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                RateLimitBucket.scope,
                RateLimitBucket.principal_fingerprint,
                RateLimitBucket.client_fingerprint,
            ],
            set_={
                "window_started_at": case((expired, now), else_=RateLimitBucket.window_started_at),
                "request_count": case((expired, 1), else_=RateLimitBucket.request_count + 1),
                "expires_at": case((expired, expires_at), else_=RateLimitBucket.expires_at),
                "updated_at": now,
            },
        )
        .returning(RateLimitBucket.request_count, RateLimitBucket.expires_at)
    )
    row = (await session.execute(statement)).one()
    return RateLimitResult(count=row.request_count, expires_at=row.expires_at)


def rate_limit_error(expires_at: datetime, now: datetime) -> ServiceError:
    retry_after = max(1, math.ceil((expires_at - now).total_seconds()))
    return ServiceError(
        429,
        "RATE_LIMITED",
        "잠시 후 다시 시도해 주세요.",
        headers={"Retry-After": str(retry_after)},
    )
