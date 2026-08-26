import hashlib
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyRecord
from app.security import canonical_json_sha256
from app.services.lifecycle import RETENTION_PERIOD


def payload_sha256(payload: BaseModel) -> str:
    return canonical_json_sha256(payload.model_dump(mode="json"))


def completed_idempotency_record(
    *,
    principal_digest: bytes,
    operation: str,
    client_request_id: UUID,
    payload_sha256: str,
    response_status: int,
    now: datetime,
    safe_failure_code: str | None = None,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        principal_digest=principal_digest,
        operation=operation,
        client_request_id=client_request_id,
        payload_sha256=payload_sha256,
        response_status=response_status,
        safe_failure_code=safe_failure_code,
        processing_status="COMPLETED",
        created_at=now,
        completed_at=now,
        purge_at=now + RETENTION_PERIOD,
    )


async def lock_idempotency_key(
    session: AsyncSession,
    principal_digest: bytes,
    operation: str,
    client_request_id: UUID,
) -> None:
    digest = hashlib.sha256(
        principal_digest + operation.encode() + client_request_id.bytes
    ).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
