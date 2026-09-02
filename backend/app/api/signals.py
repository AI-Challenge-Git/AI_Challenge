from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    customer_principal,
    get_clock,
    operator_principal,
    rate_limit_client_identifier,
)
from app.codes import SignalStatus
from app.config import Settings, get_settings
from app.db import get_session
from app.schemas import (
    OperationalMetricsResponse,
    OperatorAcknowledgeSignalRequest,
    OperatorApproveSignalPolicyRequest,
    OperatorCloseSignalRequest,
    OperatorMergeSignalsRequest,
    OperatorOfficialNoticeRequest,
    OperatorSignalListResponse,
    OperatorSignalMutationResponse,
    OperatorSignalPolicyApprovalResponse,
    OperatorSplitSignalRequest,
    ProblemDetails,
    SignalDashboardResponse,
)
from app.services.agents import AgentPrincipal
from app.services.operations import collect_operational_metrics
from app.services.signals import (
    acknowledge_signal,
    approve_signal_policy,
    close_signal,
    enforce_dashboard_rate_limit,
    link_official_notice,
    list_dashboard_signals,
    list_operator_signals,
    merge_signals,
    split_signal,
)

router = APIRouter(prefix="/api")
SIGNAL_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails, "description": "Invalid access token"},
    403: {"model": ProblemDetails, "description": "Operator role required"},
    404: {"model": ProblemDetails, "description": "Signal unavailable"},
    409: {"model": ProblemDetails, "description": "State or idempotency conflict"},
    422: {"model": ProblemDetails, "description": "Invalid request"},
}


@router.get(
    "/signals/dashboard",
    tags=["signals"],
    response_model=SignalDashboardResponse,
    responses={
        401: SIGNAL_ERRORS[401],
        422: SIGNAL_ERRORS[422],
        429: {"model": ProblemDetails, "description": "Rate limit exceeded"},
    },
)
async def dashboard(
    request: Request,
    response: Response,
    principal: Annotated[bytes, Depends(customer_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SignalDashboardResponse:
    response.headers["Cache-Control"] = "no-store"
    now = clock()
    client_identifier = rate_limit_client_identifier(request, app_env=settings.app_env)
    await enforce_dashboard_rate_limit(
        session,
        principal,
        client_identifier,
        settings,
        now=now,
    )
    return await list_dashboard_signals(session, now=now, limit=limit, offset=offset)


@router.get(
    "/operator/signals",
    tags=["operator-signals"],
    response_model=OperatorSignalListResponse,
    responses={401: SIGNAL_ERRORS[401], 403: SIGNAL_ERRORS[403], 422: SIGNAL_ERRORS[422]},
)
async def operator_signals(
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    status: SignalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> OperatorSignalListResponse:
    del principal
    response.headers["Cache-Control"] = "no-store"
    return await list_operator_signals(
        session,
        now=clock(),
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/operator/signal-policies/approve",
    tags=["operator-signals"],
    response_model=OperatorSignalPolicyApprovalResponse,
    responses=SIGNAL_ERRORS,
)
async def approve_policy(
    request_body: OperatorApproveSignalPolicyRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperatorSignalPolicyApprovalResponse:
    response.headers["Cache-Control"] = "no-store"
    return await approve_signal_policy(session, principal, request_body, now=clock())


@router.get(
    "/operator/operations/metrics",
    tags=["operator-operations"],
    response_model=OperationalMetricsResponse,
    responses={401: SIGNAL_ERRORS[401], 403: SIGNAL_ERRORS[403]},
)
async def operations_metrics(
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperationalMetricsResponse:
    del principal
    response.headers["Cache-Control"] = "no-store"
    return await collect_operational_metrics(session, now=clock())


@router.post(
    "/operator/signals/acknowledge",
    tags=["operator-signals"],
    response_model=OperatorSignalMutationResponse,
    responses=SIGNAL_ERRORS,
)
async def acknowledge(
    request_body: OperatorAcknowledgeSignalRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperatorSignalMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await acknowledge_signal(session, principal, request_body, now=clock())


@router.post(
    "/operator/signals/close",
    tags=["operator-signals"],
    response_model=OperatorSignalMutationResponse,
    responses=SIGNAL_ERRORS,
)
async def close(
    request_body: OperatorCloseSignalRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperatorSignalMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await close_signal(session, principal, request_body, now=clock())


@router.post(
    "/operator/signals/official-notice",
    tags=["operator-signals"],
    response_model=OperatorSignalMutationResponse,
    responses=SIGNAL_ERRORS,
)
async def official_notice(
    request_body: OperatorOfficialNoticeRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperatorSignalMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await link_official_notice(session, principal, request_body, now=clock())


@router.post(
    "/operator/signals/merge",
    tags=["operator-signals"],
    response_model=OperatorSignalMutationResponse,
    responses=SIGNAL_ERRORS,
)
async def merge(
    request_body: OperatorMergeSignalsRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperatorSignalMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await merge_signals(session, principal, request_body, now=clock())


@router.post(
    "/operator/signals/split",
    tags=["operator-signals"],
    response_model=OperatorSignalMutationResponse,
    responses=SIGNAL_ERRORS,
)
async def split(
    request_body: OperatorSplitSignalRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(operator_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OperatorSignalMutationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await split_signal(session, principal, request_body, now=clock())
