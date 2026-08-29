import asyncio
import hashlib
import hmac
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql.elements import ColumnElement

from app.codes import (
    AgentRole,
    AuditOutcome,
    IssueType,
    OrderAction,
    OrderType,
    RateLimitScope,
    SubmissionStatus,
    VerificationStatus,
)
from app.config import Settings
from app.errors import ServiceError
from app.models import (
    AgentAccessToken,
    AgentAccount,
    AgentVerification,
    AuditLog,
    ConsultationCard,
    IdempotencyRecord,
    RateLimitBucket,
    Report,
    TechnicalSymptom,
)
from app.schemas import (
    AgentLoginRequest,
    AgentLoginResponse,
    AgentTechnicalDetail,
    AgentVerificationRequest,
    AgentVerificationResponse,
    ConsultationCardDetail,
    ConsultationCardListItem,
    ConsultationCardListResponse,
    ConsultationCardLookupRequest,
    ConsultationConfirmation,
    VerificationFieldName,
    VerificationFieldResult,
)
from app.security import (
    InvalidSessionTokenError,
    SensitiveInputError,
    ensure_confirmation_strings_are_safe,
    keyed_fingerprint,
    make_opaque_token,
    opaque_token_digest,
    reference_digest,
    verify_password,
)
from app.services.idempotency import (
    completed_idempotency_record,
    lock_idempotency_key,
    payload_sha256,
)
from app.services.lifecycle import card_is_accessible
from app.services.symbols import validate_symbol

Sleeper = Callable[[float], Awaitable[None]]
CONSULTATION_SAFETY_NOTICE = (
    "상담 준비카드는 주문 접수·체결 증빙이 아닙니다. "
    "재주문 전 공식 채널에서 주문 상태를 확인해야 합니다."
)
_VERIFICATION_OPERATION = "VERIFY_CONSULTATION_CARD"
_STATUS_PRIORITY = {
    VerificationStatus.MATCHED: 0,
    VerificationStatus.NEEDS_CONFIRMATION: 1,
    VerificationStatus.IMPORTANT: 2,
}


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    agent_id: UUID
    agent_label: str
    role: AgentRole


@dataclass(frozen=True, slots=True)
class _RateLimitResult:
    count: int
    expires_at: datetime


def _fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _agent_resource(agent_id: UUID) -> str:
    return _fingerprint(b"agent:" + agent_id.bytes)


def _card_resource(card_id: UUID) -> str:
    return _fingerprint(b"card:" + card_id.bytes)


def _generic_resource(event: str) -> str:
    return hashlib.sha256(event.encode()).hexdigest()


def _agent_principal_digest(agent_id: UUID) -> bytes:
    return hashlib.sha256(b"agent-account:" + agent_id.bytes).digest()


def _audit(
    *,
    action: str,
    outcome: AuditOutcome,
    resource_fingerprint: str,
    now: datetime,
    actor_id: UUID | None = None,
) -> AuditLog:
    return AuditLog(
        actor_id=actor_id,
        actor_type="agent" if actor_id is not None else "anonymous_agent_login",
        action=action,
        outcome=outcome.value,
        resource_fingerprint=resource_fingerprint,
        created_at=now,
    )


async def resolve_agent_token(
    session: AsyncSession,
    token: str,
    settings: Settings,
    *,
    now: datetime,
) -> AgentPrincipal:
    try:
        digest = opaque_token_digest(
            token,
            settings.agent_token_hmac_key.get_secret_value().encode(),
        )
    except (InvalidSessionTokenError, ValueError) as exc:
        raise ServiceError(401, "INVALID_AGENT_TOKEN", "인증 정보가 올바르지 않습니다.") from exc

    access_token = await session.scalar(
        select(AgentAccessToken)
        .where(AgentAccessToken.token_digest == digest)
        .options(joinedload(AgentAccessToken.agent))
    )
    if (
        access_token is None
        or not hmac.compare_digest(access_token.token_digest, digest)
        or access_token.revoked_at is not None
        or now >= access_token.expires_at
        or not access_token.agent.is_active
    ):
        raise ServiceError(401, "INVALID_AGENT_TOKEN", "인증 정보가 올바르지 않습니다.")
    return AgentPrincipal(
        agent_id=access_token.agent.id,
        agent_label=access_token.agent.agent_label,
        role=AgentRole(access_token.agent.role),
    )


async def _current_rate_limit(
    session: AsyncSession,
    *,
    scope: RateLimitScope,
    principal_fingerprint: bytes,
    client_fingerprint: bytes,
    now: datetime,
) -> _RateLimitResult | None:
    row = (
        await session.execute(
            select(RateLimitBucket.request_count, RateLimitBucket.expires_at).where(
                RateLimitBucket.scope == scope.value,
                RateLimitBucket.principal_fingerprint == principal_fingerprint,
                RateLimitBucket.client_fingerprint == client_fingerprint,
                RateLimitBucket.expires_at > now,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return _RateLimitResult(count=row.request_count, expires_at=row.expires_at)


async def _consume_rate_limit(
    session: AsyncSession,
    *,
    scope: RateLimitScope,
    principal_fingerprint: bytes,
    client_fingerprint: bytes,
    now: datetime,
    window_seconds: int,
) -> _RateLimitResult:
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
    return _RateLimitResult(count=row.request_count, expires_at=row.expires_at)


async def _clear_rate_limit(
    session: AsyncSession,
    *,
    scope: RateLimitScope,
    principal_fingerprint: bytes,
    client_fingerprint: bytes,
) -> None:
    await session.execute(
        delete(RateLimitBucket).where(
            RateLimitBucket.scope == scope.value,
            RateLimitBucket.principal_fingerprint == principal_fingerprint,
            RateLimitBucket.client_fingerprint == client_fingerprint,
        )
    )


def _rate_limit_error(expires_at: datetime, now: datetime) -> ServiceError:
    retry_after = max(1, math.ceil((expires_at - now).total_seconds()))
    return ServiceError(
        429,
        "RATE_LIMITED",
        "잠시 후 다시 시도해 주세요.",
        headers={"Retry-After": str(retry_after)},
    )


async def login_agent(
    session: AsyncSession,
    request: AgentLoginRequest,
    client_identifier: str,
    settings: Settings,
    *,
    now: datetime,
    sleeper: Sleeper = asyncio.sleep,
) -> AgentLoginResponse:
    rate_key = settings.rate_limit_hmac_key.get_secret_value().encode()
    employee_fingerprint = keyed_fingerprint(request.employee_id, "agent-login-employee", rate_key)
    client_fingerprint = keyed_fingerprint(client_identifier, "agent-login-client", rate_key)

    blocked: _RateLimitResult | None = None
    password_hash: str | None = None
    account_id: UUID | None = None
    account_active = False
    async with session.begin():
        current = await _current_rate_limit(
            session,
            scope=RateLimitScope.AGENT_LOGIN_FAILURE,
            principal_fingerprint=employee_fingerprint,
            client_fingerprint=client_fingerprint,
            now=now,
        )
        if current is not None and current.count >= settings.agent_login_failure_limit:
            blocked = current
            session.add(
                _audit(
                    action="AGENT_LOGIN_RATE_LIMITED",
                    outcome=AuditOutcome.RATE_LIMITED,
                    resource_fingerprint=_generic_resource("agent-login"),
                    now=now,
                )
            )
        else:
            account = await session.scalar(
                select(AgentAccount).where(AgentAccount.employee_id == request.employee_id)
            )
            if account is not None:
                password_hash = account.password_hash
                account_id = account.id
                account_active = account.is_active

    if blocked is not None:
        await sleeper(settings.agent_login_failure_delay_ms / 1000)
        raise _rate_limit_error(blocked.expires_at, now)

    password_valid = await asyncio.to_thread(
        verify_password,
        request.password.get_secret_value(),
        password_hash,
    )
    if password_valid and account_active and account_id is not None:
        token = make_opaque_token()
        expires_at = now + timedelta(minutes=settings.agent_access_token_ttl_minutes)
        async with session.begin():
            account = await session.scalar(
                select(AgentAccount).where(AgentAccount.id == account_id).with_for_update()
            )
            if (
                account is not None
                and account.is_active
                and hmac.compare_digest(account.password_hash, password_hash or "")
            ):
                await _clear_rate_limit(
                    session,
                    scope=RateLimitScope.AGENT_LOGIN_FAILURE,
                    principal_fingerprint=employee_fingerprint,
                    client_fingerprint=client_fingerprint,
                )
                session.add_all(
                    (
                        AgentAccessToken(
                            agent_id=account.id,
                            token_digest=opaque_token_digest(
                                token,
                                settings.agent_token_hmac_key.get_secret_value().encode(),
                            ),
                            expires_at=expires_at,
                            created_at=now,
                        ),
                        _audit(
                            actor_id=account.id,
                            action="AGENT_LOGIN_SUCCEEDED",
                            outcome=AuditOutcome.SUCCESS,
                            resource_fingerprint=_agent_resource(account.id),
                            now=now,
                        ),
                    )
                )
                return AgentLoginResponse(
                    access_token=token,
                    token_type="bearer",
                    expires_at=expires_at,
                    agent_label=account.agent_label,
                    role=AgentRole(account.role),
                )

    async with session.begin():
        rate = await _consume_rate_limit(
            session,
            scope=RateLimitScope.AGENT_LOGIN_FAILURE,
            principal_fingerprint=employee_fingerprint,
            client_fingerprint=client_fingerprint,
            now=now,
            window_seconds=settings.agent_login_failure_window_seconds,
        )
        limited = rate.count > settings.agent_login_failure_limit
        session.add(
            _audit(
                action="AGENT_LOGIN_RATE_LIMITED" if limited else "AGENT_LOGIN_FAILED",
                outcome=AuditOutcome.RATE_LIMITED if limited else AuditOutcome.FAILURE,
                resource_fingerprint=_generic_resource("agent-login"),
                now=now,
            )
        )
    await sleeper(settings.agent_login_failure_delay_ms / 1000)
    if limited:
        raise _rate_limit_error(rate.expires_at, now)
    raise ServiceError(401, "INVALID_CREDENTIALS", "사번 또는 비밀번호를 확인해 주세요.")


async def list_consultation_cards(
    session: AsyncSession,
    principal: AgentPrincipal,
    *,
    limit: int,
    offset: int,
    now: datetime,
) -> ConsultationCardListResponse:
    latest_status = (
        select(AgentVerification.overall_status)
        .where(AgentVerification.card_id == ConsultationCard.id)
        .order_by(AgentVerification.created_at.desc(), AgentVerification.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    async with session.begin():
        rows = (
            await session.execute(
                select(
                    ConsultationCard.id,
                    Report.received_at,
                    ConsultationCard.created_at,
                    ConsultationCard.expires_at,
                    TechnicalSymptom.symptom,
                    latest_status.label("verification_status"),
                )
                .join(Report, ConsultationCard.report_id == Report.id)
                .join(TechnicalSymptom, TechnicalSymptom.report_id == Report.id)
                .where(
                    Report.purge_at > now,
                    Report.status == "CONFIRMED",
                    ConsultationCard.expires_at.is_not(None),
                    ConsultationCard.reference_digest.is_not(None),
                )
                .order_by(Report.received_at.desc(), ConsultationCard.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        session.add(
            _audit(
                actor_id=principal.agent_id,
                action="AGENT_CARD_LIST_VIEWED",
                outcome=AuditOutcome.SUCCESS,
                resource_fingerprint=_agent_resource(principal.agent_id),
                now=now,
            )
        )

    items: list[ConsultationCardListItem] = []
    for row in rows:
        accessible = card_is_accessible(row.expires_at, now=now)
        verification_status = (
            VerificationStatus(row.verification_status)
            if row.verification_status is not None
            else None
        )
        items.append(
            ConsultationCardListItem(
                card_id=row.id,
                received_at=row.received_at,
                issued_at=row.created_at,
                expires_at=row.expires_at,
                expired=not accessible,
                can_open=accessible,
                consultation_status="VERIFIED" if verification_status is not None else "OPEN",
                technical_symptom=row.symptom,
                verification_status=verification_status,
            )
        )
    return ConsultationCardListResponse(items=items, limit=limit, offset=offset)


def _selector_conditions(
    request: ConsultationCardLookupRequest | AgentVerificationRequest,
    settings: Settings,
) -> tuple[ColumnElement[bool], ...]:
    if request.card_id is not None:
        return (ConsultationCard.id == request.card_id,)
    if request.reference_number is None:
        raise ValueError("validated selector is missing")
    digest = reference_digest(
        request.reference_number,
        settings.reference_hmac_key.get_secret_value().encode(),
    )
    return (ConsultationCard.reference_digest == digest,)


async def _load_card(
    session: AsyncSession,
    request: ConsultationCardLookupRequest | AgentVerificationRequest,
    settings: Settings,
    *,
    now: datetime,
    for_update: bool = False,
) -> ConsultationCard | None:
    statement = (
        select(ConsultationCard)
        .join(Report, ConsultationCard.report_id == Report.id)
        .where(*_selector_conditions(request, settings), Report.purge_at > now)
        .options(
            joinedload(ConsultationCard.report).joinedload(Report.technical_symptom),
            joinedload(ConsultationCard.report).joinedload(Report.attachment),
        )
    )
    if for_update:
        statement = statement.with_for_update(of=ConsultationCard)
    return cast(ConsultationCard | None, await session.scalar(statement))


async def _latest_verification_status(
    session: AsyncSession,
    card_id: UUID,
) -> VerificationStatus | None:
    value = await session.scalar(
        select(AgentVerification.overall_status)
        .where(AgentVerification.card_id == card_id)
        .order_by(AgentVerification.created_at.desc(), AgentVerification.id.desc())
        .limit(1)
    )
    return VerificationStatus(value) if value is not None else None


def _card_detail(
    card: ConsultationCard,
    technical: TechnicalSymptom,
    verification_status: VerificationStatus | None,
) -> ConsultationCardDetail:
    if card.expires_at is None:
        raise ServiceError(404, "CARD_NOT_FOUND", "상담카드를 찾을 수 없습니다.")
    return ConsultationCardDetail(
        card_id=card.id,
        created_at=card.created_at,
        expires_at=card.expires_at,
        technical=AgentTechnicalDetail(
            issue_type=IssueType(technical.issue_type),
            symptom=technical.symptom,
            submission_status=SubmissionStatus(technical.submission_status),
            error_code=technical.error_code,
            reported_occurred_at=technical.reported_occurred_at,
        ),
        consultation=ConsultationConfirmation(
            action=OrderAction(card.action),
            symbol_name=card.symbol_name,
            symbol_code=card.symbol_code,
            quantity=card.quantity,
            order_type=OrderType(card.order_type),
            price_krw=card.price_krw,
            attempted_at=card.attempted_at,
        ),
        verification_status=verification_status,
        safety_notice=CONSULTATION_SAFETY_NOTICE,
        has_attachment=card.report.attachment is not None,
        related_signals=[],
    )


async def lookup_consultation_card(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: ConsultationCardLookupRequest,
    client_identifier: str,
    settings: Settings,
    *,
    now: datetime,
    sleeper: Sleeper = asyncio.sleep,
) -> ConsultationCardDetail:
    rate_key = settings.rate_limit_hmac_key.get_secret_value().encode()
    principal_fingerprint = keyed_fingerprint(
        str(principal.agent_id), "agent-lookup-principal", rate_key
    )
    client_fingerprint = keyed_fingerprint(client_identifier, "agent-lookup-client", rate_key)
    error: ServiceError | None = None
    response: ConsultationCardDetail | None = None
    async with session.begin():
        rate = await _consume_rate_limit(
            session,
            scope=RateLimitScope.AGENT_CARD_LOOKUP,
            principal_fingerprint=principal_fingerprint,
            client_fingerprint=client_fingerprint,
            now=now,
            window_seconds=settings.agent_lookup_window_seconds,
        )
        if rate.count > settings.agent_lookup_limit:
            session.add(
                _audit(
                    actor_id=principal.agent_id,
                    action="AGENT_CARD_LOOKUP_RATE_LIMITED",
                    outcome=AuditOutcome.RATE_LIMITED,
                    resource_fingerprint=_agent_resource(principal.agent_id),
                    now=now,
                )
            )
            error = _rate_limit_error(rate.expires_at, now)
        else:
            card = await _load_card(session, request, settings, now=now)
            if card is None or not card_is_accessible(card.expires_at, now=now):
                action = (
                    "AGENT_CARD_LOOKUP_EXPIRED"
                    if card is not None and card.expires_at is not None and now >= card.expires_at
                    else "AGENT_CARD_LOOKUP_FAILED"
                )
                session.add(
                    _audit(
                        actor_id=principal.agent_id,
                        action=action,
                        outcome=AuditOutcome.FAILURE,
                        resource_fingerprint=_agent_resource(principal.agent_id),
                        now=now,
                    )
                )
                error = ServiceError(404, "CARD_NOT_FOUND", "상담카드를 찾을 수 없습니다.")
            else:
                technical = card.report.technical_symptom
                if technical is None:
                    raise ServiceError(503, "CARD_UNAVAILABLE", "상담카드를 사용할 수 없습니다.")
                verification_status = await _latest_verification_status(session, card.id)
                response = _card_detail(card, technical, verification_status)
                session.add(
                    _audit(
                        actor_id=principal.agent_id,
                        action="AGENT_CARD_LOOKUP_SUCCEEDED",
                        outcome=AuditOutcome.SUCCESS,
                        resource_fingerprint=_card_resource(card.id),
                        now=now,
                    )
                )

    if error is not None:
        await sleeper(settings.agent_lookup_failure_delay_ms / 1000)
        raise error
    if response is None:
        raise ServiceError(503, "CARD_UNAVAILABLE", "상담카드를 사용할 수 없습니다.")
    return response


def _compare_value(
    field: VerificationFieldName,
    customer_value: str | int | None,
    agent_value: str | int | None,
) -> VerificationFieldResult:
    unknown_values: tuple[object, ...] = (None, "UNKNOWN")
    if customer_value in unknown_values or agent_value in unknown_values:
        status = VerificationStatus.NEEDS_CONFIRMATION
    elif customer_value != agent_value:
        status = VerificationStatus.IMPORTANT
    else:
        status = VerificationStatus.MATCHED
    return VerificationFieldResult(
        field=field,
        status=status,
        customer_value=customer_value,
        agent_value=agent_value,
    )


def _verification_response(
    verification: AgentVerification,
    card: ConsultationCard,
    technical: TechnicalSymptom,
) -> AgentVerificationResponse:
    fields = _comparison_fields(verification, card, technical)
    mismatch_fields = [
        result.field for result in fields if result.status is VerificationStatus.IMPORTANT
    ]
    return AgentVerificationResponse(
        verification_id=verification.id,
        status=VerificationStatus(verification.overall_status),
        fields=fields,
        mismatch_fields=mismatch_fields,
        saved_at=verification.created_at,
    )


def _comparison_fields(
    verification: AgentVerification,
    card: ConsultationCard,
    technical: TechnicalSymptom,
) -> list[VerificationFieldResult]:
    field_values: list[tuple[VerificationFieldName, str | int | None, str | int | None]] = [
        ("action", card.action, verification.action),
        ("symbol_name", card.symbol_name, verification.symbol_name),
        ("symbol_code", card.symbol_code, verification.symbol_code),
        ("quantity", card.quantity, verification.quantity),
        ("order_type", card.order_type, verification.order_type),
        ("price_krw", card.price_krw, verification.price_krw),
        ("submission_status", technical.submission_status, verification.submission_status),
    ]
    return [_compare_value(*values) for values in field_values]


def _overall_verification_status(
    fields: list[VerificationFieldResult],
    *,
    order_history_checked: bool,
) -> VerificationStatus:
    statuses = [field.status for field in fields]
    if not order_history_checked:
        statuses.append(VerificationStatus.NEEDS_CONFIRMATION)
    return max(statuses, key=_STATUS_PRIORITY.__getitem__)


async def save_agent_verification(
    session: AsyncSession,
    principal: AgentPrincipal,
    request: AgentVerificationRequest,
    settings: Settings,
    *,
    now: datetime,
) -> AgentVerificationResponse:
    try:
        ensure_confirmation_strings_are_safe(request.symbol_name, request.symbol_code)
    except SensitiveInputError as exc:
        raise ServiceError(
            422, "SENSITIVE_CONFIRMATION", "확인값에 민감정보를 넣을 수 없습니다."
        ) from exc

    principal_digest = _agent_principal_digest(principal.agent_id)
    payload_digest = payload_sha256(request)
    error: ServiceError | None = None
    response: AgentVerificationResponse | None = None
    async with session.begin():
        await lock_idempotency_key(
            session,
            principal_digest,
            _VERIFICATION_OPERATION,
            request.client_request_id,
        )
        replay = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.principal_digest == principal_digest,
                IdempotencyRecord.operation == _VERIFICATION_OPERATION,
                IdempotencyRecord.client_request_id == request.client_request_id,
            )
        )
        if replay is not None and not hmac.compare_digest(replay.payload_sha256, payload_digest):
            session.add(
                _audit(
                    actor_id=principal.agent_id,
                    action="AGENT_VERIFICATION_CONFLICT",
                    outcome=AuditOutcome.CONFLICT,
                    resource_fingerprint=_agent_resource(principal.agent_id),
                    now=now,
                )
            )
            error = ServiceError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 ID의 내용이 다릅니다.")
        else:
            card = await _load_card(session, request, settings, now=now, for_update=True)
            if card is None or not card_is_accessible(card.expires_at, now=now):
                session.add(
                    _audit(
                        actor_id=principal.agent_id,
                        action="AGENT_VERIFICATION_FAILED",
                        outcome=AuditOutcome.FAILURE,
                        resource_fingerprint=_agent_resource(principal.agent_id),
                        now=now,
                    )
                )
                error = ServiceError(404, "CARD_NOT_FOUND", "상담카드를 찾을 수 없습니다.")
            else:
                technical = card.report.technical_symptom
                if technical is None:
                    raise ServiceError(503, "CARD_UNAVAILABLE", "상담카드를 사용할 수 없습니다.")
                if replay is not None:
                    verification = await session.scalar(
                        select(AgentVerification).where(
                            AgentVerification.agent_id == principal.agent_id,
                            AgentVerification.client_request_id == request.client_request_id,
                        )
                    )
                    if verification is None:
                        raise ServiceError(
                            503, "IDEMPOTENCY_UNAVAILABLE", "저장 결과를 사용할 수 없습니다."
                        )
                    response = _verification_response(verification, card, technical)
                    session.add(
                        _audit(
                            actor_id=principal.agent_id,
                            action="AGENT_VERIFICATION_REPLAYED",
                            outcome=AuditOutcome.REPLAY,
                            resource_fingerprint=_card_resource(card.id),
                            now=now,
                        )
                    )
                else:
                    symbol_master_version_id = await validate_symbol(
                        session,
                        symbol_name=request.symbol_name,
                        symbol_code=request.symbol_code,
                    )
                    temporary = AgentVerification(
                        card_id=card.id,
                        agent_id=principal.agent_id,
                        client_request_id=request.client_request_id,
                        action=request.action.value,
                        symbol_name=request.symbol_name,
                        symbol_code=request.symbol_code,
                        symbol_master_version_id=symbol_master_version_id,
                        quantity=request.quantity,
                        order_type=request.order_type.value,
                        price_krw=request.price_krw,
                        submission_status=request.submission_status.value,
                        order_history_checked=request.order_history_checked,
                        overall_status=VerificationStatus.MATCHED.value,
                        created_at=now,
                    )
                    fields = _comparison_fields(temporary, card, technical)
                    temporary.overall_status = _overall_verification_status(
                        fields,
                        order_history_checked=request.order_history_checked,
                    ).value
                    session.add(temporary)
                    await session.flush()
                    session.add_all(
                        (
                            completed_idempotency_record(
                                principal_digest=principal_digest,
                                operation=_VERIFICATION_OPERATION,
                                client_request_id=request.client_request_id,
                                payload_sha256=payload_digest,
                                response_status=200,
                                now=now,
                            ),
                            _audit(
                                actor_id=principal.agent_id,
                                action="AGENT_VERIFICATION_STORED",
                                outcome=AuditOutcome.SUCCESS,
                                resource_fingerprint=_card_resource(card.id),
                                now=now,
                            ),
                        )
                    )
                    response = _verification_response(temporary, card, technical)

    if error is not None:
        raise error
    if response is None:
        raise ServiceError(503, "VERIFICATION_UNAVAILABLE", "저장 결과를 사용할 수 없습니다.")
    return response
