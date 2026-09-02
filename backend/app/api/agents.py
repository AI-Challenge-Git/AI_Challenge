from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    agent_principal,
    get_clock,
    get_security_sleeper,
    rate_limit_client_identifier,
)
from app.attachments import AttachmentStore, get_attachment_store
from app.config import Settings, get_settings
from app.db import get_session
from app.schemas import (
    AgentLoginRequest,
    AgentLoginResponse,
    AgentSignalVerificationRequest,
    AgentSignalVerificationResponse,
    AgentVerificationRequest,
    AgentVerificationResponse,
    ConsultationCardDetail,
    ConsultationCardListResponse,
    ConsultationCardLookupRequest,
    ProblemDetails,
)
from app.services.agents import (
    AgentPrincipal,
    Sleeper,
    list_consultation_cards,
    login_agent,
    lookup_consultation_card,
    save_agent_signal_verification,
    save_agent_verification,
)

router = APIRouter(prefix="/api")
AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails, "description": "Invalid credentials or access token"},
    403: {"model": ProblemDetails, "description": "Agent role required"},
    422: {"model": ProblemDetails, "description": "Invalid request"},
    429: {"model": ProblemDetails, "description": "Rate limit exceeded"},
}


@router.post(
    "/auth/login",
    tags=["agent-auth"],
    response_model=AgentLoginResponse,
    responses={
        401: AUTH_ERRORS[401],
        422: AUTH_ERRORS[422],
        429: AUTH_ERRORS[429],
    },
)
async def login(
    request_body: AgentLoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    sleeper: Annotated[Sleeper, Depends(get_security_sleeper)],
) -> AgentLoginResponse:
    response.headers["Cache-Control"] = "no-store"
    return await login_agent(
        session,
        request_body,
        rate_limit_client_identifier(request, app_env=settings.app_env),
        settings,
        now=clock(),
        sleeper=sleeper,
    )


@router.get(
    "/agent/consultation-cards",
    tags=["agent-cards"],
    response_model=ConsultationCardListResponse,
    responses=AUTH_ERRORS,
)
async def list_cards(
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(agent_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ConsultationCardListResponse:
    response.headers["Cache-Control"] = "no-store"
    return await list_consultation_cards(
        session,
        principal,
        limit=limit,
        offset=offset,
        now=clock(),
    )


@router.post(
    "/consultation-cards/lookup",
    tags=["agent-cards"],
    response_model=ConsultationCardDetail,
    responses={**AUTH_ERRORS, 404: {"model": ProblemDetails, "description": "Card unavailable"}},
)
async def lookup_card(
    request_body: ConsultationCardLookupRequest,
    request: Request,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(agent_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    sleeper: Annotated[Sleeper, Depends(get_security_sleeper)],
    attachment_store: Annotated[AttachmentStore, Depends(get_attachment_store)],
) -> ConsultationCardDetail:
    response.headers["Cache-Control"] = "no-store"
    return await lookup_consultation_card(
        session,
        principal,
        request_body,
        rate_limit_client_identifier(request, app_env=settings.app_env),
        settings,
        attachment_store,
        now=clock(),
        sleeper=sleeper,
    )


@router.post(
    "/consultation-cards/verifications",
    tags=["agent-cards"],
    response_model=AgentVerificationResponse,
    responses={
        **AUTH_ERRORS,
        404: {"model": ProblemDetails, "description": "Card unavailable"},
        409: {"model": ProblemDetails, "description": "Idempotency conflict"},
    },
)
async def verify_card(
    request_body: AgentVerificationRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(agent_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AgentVerificationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await save_agent_verification(
        session,
        principal,
        request_body,
        settings,
        now=clock(),
    )


@router.post(
    "/consultation-cards/signal-verifications",
    tags=["agent-cards"],
    response_model=AgentSignalVerificationResponse,
    responses={
        **AUTH_ERRORS,
        404: {"model": ProblemDetails, "description": "Card or signal unavailable"},
        409: {"model": ProblemDetails, "description": "Locked result conflict"},
        503: {"model": ProblemDetails, "description": "Signal relevance unavailable"},
    },
)
async def verify_signal_relevance(
    request_body: AgentSignalVerificationRequest,
    response: Response,
    principal: Annotated[AgentPrincipal, Depends(agent_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AgentSignalVerificationResponse:
    response.headers["Cache-Control"] = "no-store"
    return await save_agent_signal_verification(
        session,
        principal,
        request_body,
        settings,
        now=clock(),
    )
