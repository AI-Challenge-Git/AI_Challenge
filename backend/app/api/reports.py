from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import DualExtractor, get_dual_extractor
from app.api.dependencies import customer_principal
from app.config import Settings, get_settings
from app.db import get_session
from app.schemas import (
    DeleteConsultationCardRequest,
    DiscardReportRequest,
    ProblemDetails,
    ReportAnalysisResponse,
    ReportConfirmationRequest,
    ReportConfirmedResponse,
    ReportCreateRequest,
)
from app.services.reports import analyze_report, confirm_report, delete_report, discard_report

router = APIRouter(prefix="/api", tags=["reports"])
COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails, "description": "Invalid customer session"},
    409: {"model": ProblemDetails, "description": "State or idempotency conflict"},
    422: {"model": ProblemDetails, "description": "Invalid or sensitive input"},
    503: {"model": ProblemDetails, "description": "Required service data unavailable"},
}


@router.post(
    "/reports/analyze",
    response_model=ReportAnalysisResponse,
    responses={**COMMON_ERRORS, 413: {"model": ProblemDetails, "description": "Body too large"}},
)
async def analyze(
    request: ReportCreateRequest,
    principal: Annotated[bytes, Depends(customer_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    extractor: Annotated[DualExtractor, Depends(get_dual_extractor)],
    response: Response,
) -> ReportAnalysisResponse:
    response.headers["Cache-Control"] = "no-store"
    return await analyze_report(session, principal, request, settings, extractor)


@router.post(
    "/reports",
    response_model=ReportConfirmedResponse,
    responses={
        **COMMON_ERRORS,
        404: {"model": ProblemDetails, "description": "Analysis not found"},
    },
)
async def confirm(
    request: ReportConfirmationRequest,
    principal: Annotated[bytes, Depends(customer_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> ReportConfirmedResponse:
    response.headers["Cache-Control"] = "no-store"
    return await confirm_report(session, principal, request, settings)


@router.delete(
    "/reports",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERRORS,
        404: {"model": ProblemDetails, "description": "Analysis not found"},
    },
)
async def discard(
    request: DiscardReportRequest,
    principal: Annotated[bytes, Depends(customer_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await discard_report(session, principal, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.delete(
    "/consultation-cards",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**COMMON_ERRORS, 404: {"model": ProblemDetails, "description": "Card not found"}},
)
async def delete_card(
    request: DeleteConsultationCardRequest,
    principal: Annotated[bytes, Depends(customer_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    await delete_report(session, principal, request, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
