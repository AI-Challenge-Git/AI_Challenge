from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.ai import DualExtractor, get_dual_extractor
from app.api.dependencies import customer_principal
from app.attachments import (
    MAX_ATTACHMENT_BYTES,
    InvalidAttachmentError,
    LocalAttachmentStore,
    PreparedAttachment,
    get_attachment_store,
    sanitize_attachment,
)
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import ServiceError
from app.schemas import (
    DeleteConsultationCardRequest,
    DiscardReportRequest,
    ProblemDetails,
    ReportAnalysisResponse,
    ReportConfirmationRequest,
    ReportConfirmedResponse,
    ReportCreateRequest,
)
from app.services.lifecycle import process_object_deletion_jobs
from app.services.reports import (
    analyze_report,
    confirm_report,
    delete_report,
    discard_report,
    include_attachment_preview,
)

router = APIRouter(prefix="/api", tags=["reports"])
COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails, "description": "Invalid customer session"},
    409: {"model": ProblemDetails, "description": "State or idempotency conflict"},
    422: {"model": ProblemDetails, "description": "Invalid or sensitive input"},
    503: {"model": ProblemDetails, "description": "Required service data unavailable"},
}
ANALYZE_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "application/json": {"schema": ReportCreateRequest.model_json_schema()},
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "text",
                    "client_request_id",
                    "screenshot",
                    "screenshot_redacted_confirmed",
                ],
                "properties": {
                    "text": {"type": "string"},
                    "client_request_id": {"type": "string", "format": "uuid4"},
                    "screenshot": {
                        "type": "string",
                        "format": "binary",
                        "description": "PNG, JPEG, or WebP; maximum 5 MiB",
                    },
                    "screenshot_redacted_confirmed": {
                        "type": "boolean",
                        "const": True,
                        "description": (
                            "The user confirms that sensitive image content was redacted."
                        ),
                    },
                },
            }
        },
    },
}


def _validated_create_request(value: object) -> ReportCreateRequest:
    try:
        return ReportCreateRequest.model_validate(value)
    except ValidationError as exc:
        raise ServiceError(422, "VALIDATION_ERROR", "요청 값을 확인해 주세요.") from exc


async def _parse_analyze_request(
    request: Request,
) -> tuple[ReportCreateRequest, PreparedAttachment | None]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise ServiceError(422, "VALIDATION_ERROR", "요청 값을 확인해 주세요.") from exc
        return _validated_create_request(payload), None

    if not content_type.startswith("multipart/form-data"):
        raise ServiceError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            "application/json 또는 multipart/form-data 요청만 지원합니다.",
        )

    try:
        async with request.form(
            max_files=1,
            max_fields=3,
            max_part_size=MAX_ATTACHMENT_BYTES + 1,
        ) as form:
            items = form.multi_items()
            values = dict(items)
            if values.get("screenshot_redacted_confirmed") != "true":
                raise ServiceError(
                    422,
                    "SCREENSHOT_REDACTION_REQUIRED",
                    "민감정보를 직접 가렸는지 확인해 주세요.",
                )
            if len(items) != 4 or {key for key, _ in items} != {
                "text",
                "client_request_id",
                "screenshot",
                "screenshot_redacted_confirmed",
            }:
                raise ServiceError(422, "VALIDATION_ERROR", "요청 값을 확인해 주세요.")
            upload = values["screenshot"]
            if not isinstance(upload, UploadFile):
                raise ServiceError(422, "INVALID_ATTACHMENT", "이미지 파일을 확인해 주세요.")
            create_request = _validated_create_request(
                {
                    "text": values["text"],
                    "client_request_id": values["client_request_id"],
                }
            )
            raw = await upload.read(MAX_ATTACHMENT_BYTES + 1)
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(422, "INVALID_MULTIPART", "이미지 요청을 확인해 주세요.") from exc

    try:
        return create_request, sanitize_attachment(raw)
    except InvalidAttachmentError as exc:
        raise ServiceError(422, "INVALID_ATTACHMENT", "이미지 파일을 확인해 주세요.") from exc


@router.post(
    "/reports/analyze",
    response_model=ReportAnalysisResponse,
    responses={
        **COMMON_ERRORS,
        413: {"model": ProblemDetails, "description": "Body too large"},
        415: {"model": ProblemDetails, "description": "Unsupported media type"},
    },
    openapi_extra={"requestBody": ANALYZE_REQUEST_BODY},
)
async def analyze(
    request: Request,
    principal: Annotated[bytes, Depends(customer_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    extractor: Annotated[DualExtractor, Depends(get_dual_extractor)],
    attachment_store: Annotated[LocalAttachmentStore, Depends(get_attachment_store)],
    response: Response,
) -> ReportAnalysisResponse:
    response.headers["Cache-Control"] = "no-store"
    create_request, attachment = await _parse_analyze_request(request)
    result = await analyze_report(
        session,
        principal,
        create_request,
        settings,
        extractor,
        attachment_store,
        attachment,
    )
    return await include_attachment_preview(session, principal, result, attachment_store)


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
    attachment_store: Annotated[LocalAttachmentStore, Depends(get_attachment_store)],
) -> Response:
    job_id = await discard_report(session, principal, request)
    await process_object_deletion_jobs(
        session,
        attachment_store,
        batch_size=1,
        job_ids=(job_id,) if job_id is not None else None,
    )
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
    attachment_store: Annotated[LocalAttachmentStore, Depends(get_attachment_store)],
) -> Response:
    job_id = await delete_report(session, principal, request, settings)
    await process_object_deletion_jobs(
        session,
        attachment_store,
        batch_size=1,
        job_ids=(job_id,) if job_id is not None else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
