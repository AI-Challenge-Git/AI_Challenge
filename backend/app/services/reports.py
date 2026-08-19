import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai import DualExtractor, validate_evidence_quotes
from app.attachments import (
    AttachmentStorageError,
    LocalAttachmentStore,
    PreparedAttachment,
    attachment_data_url,
)
from app.codes import AnalysisStatus, FeatureArea, ReportStatus, TechnicalChannel
from app.config import Settings
from app.errors import ServiceError
from app.models import (
    Attachment,
    AuditLog,
    ConsultationCard,
    IdempotencyRecord,
    PolicySnapshot,
    Report,
    ReportAnalysis,
    TechnicalSymptom,
)
from app.schemas import (
    AttachmentResponse,
    ConsultationCandidate,
    ConsultationCardIssued,
    DeleteConsultationCardRequest,
    DiscardReportRequest,
    ReportAnalysisCompleteResponse,
    ReportAnalysisConfirmationResponse,
    ReportAnalysisFailedResponse,
    ReportAnalysisPendingResponse,
    ReportAnalysisResponse,
    ReportConfirmationRequest,
    ReportConfirmedResponse,
    ReportCreateRequest,
    SafeError,
    TechnicalCandidate,
)
from app.security import (
    InvalidReportTextError,
    PiiDecision,
    SensitiveInputError,
    canonical_json_sha256,
    ensure_confirmation_strings_are_safe,
    make_reference_number,
    normalize_report_text,
    reference_digest,
    scan_and_mask,
)

CARD_TTL = timedelta(hours=2)


def _payload_sha256(payload: BaseModel) -> str:
    return canonical_json_sha256(payload.model_dump(mode="json"))


def _analyze_payload_sha256(
    payload: ReportCreateRequest, attachment: PreparedAttachment | None
) -> str:
    value = payload.model_dump(mode="json")
    if attachment is not None:
        value["attachment_sha256"] = attachment.sha256
    return canonical_json_sha256(value)


async def _lock_idempotency_key(
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


def _analysis_response(report: Report, analysis: ReportAnalysis) -> ReportAnalysisResponse:
    if analysis.status == AnalysisStatus.PENDING.value:
        if report.status != ReportStatus.ANALYSIS_PENDING.value:
            raise ServiceError(503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다.")
        return ReportAnalysisResponse(
            root=ReportAnalysisPendingResponse(
                analysis_id=analysis.id,
                analysis_version=analysis.version,
                status="pending",
            )
        )
    if analysis.status == AnalysisStatus.FAILED.value:
        if report.status != ReportStatus.ANALYSIS_FAILED.value or not analysis.safe_error_code:
            raise ServiceError(503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다.")
        return ReportAnalysisResponse(
            root=ReportAnalysisFailedResponse(
                analysis_id=analysis.id,
                analysis_version=analysis.version,
                status="failed",
                error=SafeError(code=analysis.safe_error_code),
            )
        )
    if analysis.status != AnalysisStatus.SUCCEEDED.value:
        raise ServiceError(503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다.")
    if report.status == ReportStatus.CONFIRMED.value:
        return ReportAnalysisResponse(
            root=ReportAnalysisCompleteResponse(
                analysis_id=analysis.id,
                analysis_version=analysis.version,
                status="complete",
            )
        )
    if report.status != ReportStatus.AWAITING_CONFIRMATION.value:
        raise ServiceError(503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다.")
    if analysis.technical_candidate is None or analysis.consultation_candidate is None:
        raise ServiceError(503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다.")
    masked_items = [
        kind for kind in ("PHONE", "ACCOUNT", "EMAIL") if f"[{kind}]" in report.masked_text
    ]
    return ReportAnalysisResponse(
        root=ReportAnalysisConfirmationResponse(
            analysis_id=analysis.id,
            analysis_version=analysis.version,
            status="confirmation",
            attachment=None,
            masked_text=report.masked_text,
            masked_items=masked_items,
            technical=TechnicalCandidate.model_validate(analysis.technical_candidate, strict=False),
            consultation=ConsultationCandidate.model_validate(
                analysis.consultation_candidate, strict=False
            ),
        )
    )


async def analyze_report(
    session: AsyncSession,
    principal_digest: bytes,
    request: ReportCreateRequest,
    settings: Settings,
    extractor: DualExtractor,
    attachment_store: LocalAttachmentStore,
    prepared_attachment: PreparedAttachment | None = None,
) -> ReportAnalysisResponse:
    try:
        normalized = normalize_report_text(request.text)
        scan = scan_and_mask(normalized)
    except (InvalidReportTextError, SensitiveInputError) as exc:
        raise ServiceError(422, "INVALID_REPORT", "제보 내용을 확인해 주세요.") from exc
    if scan.decision is PiiDecision.REJECT:
        raise ServiceError(422, "SENSITIVE_INPUT_REJECTED", "입력할 수 없는 민감정보가 있습니다.")

    payload_sha256 = _analyze_payload_sha256(request, prepared_attachment)
    attachment_object_key: str | None = None
    async with session.begin():
        await _lock_idempotency_key(
            session, principal_digest, "ANALYZE_REPORT", request.client_request_id
        )
        existing = await session.scalar(
            select(Report)
            .where(
                Report.session_digest == principal_digest,
                Report.client_request_id == request.client_request_id,
            )
            .options(selectinload(Report.analyses), selectinload(Report.attachment))
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_payload_sha256, payload_sha256):
                raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 ID의 내용이 다릅니다.")
            analysis = max(existing.analyses, key=lambda item: item.version)
            return _analysis_response(existing, analysis)

        policy = await session.scalar(
            select(PolicySnapshot).where(PolicySnapshot.version == settings.active_policy_version)
        )
        if policy is None:
            raise ServiceError(503, "POLICY_UNAVAILABLE", "현재 정책을 사용할 수 없습니다.")

        report = Report(
            session_digest=principal_digest,
            client_request_id=request.client_request_id,
            policy_snapshot_id=policy.id,
            pii_policy_version=settings.pii_policy_version,
            masked_text=scan.masked_text,
            request_payload_sha256=payload_sha256,
            status=ReportStatus.ANALYSIS_PENDING.value,
        )
        analysis = ReportAnalysis(
            report=report,
            version=1,
            schema_version=extractor.schema_version,
            taxonomy_version=extractor.taxonomy_version,
            adapter_name=extractor.adapter_name,
            model_id=extractor.model_id,
            status=AnalysisStatus.PENDING.value,
        )
        if prepared_attachment is not None:
            attachment_object_key = secrets.token_urlsafe(32)
            report.attachment = Attachment(
                object_key=attachment_object_key,
                content_type=prepared_attachment.content_type,
                byte_size=len(prepared_attachment.content),
                width=prepared_attachment.width,
                height=prepared_attachment.height,
                content_sha256=prepared_attachment.sha256,
            )
        session.add(report)
        await session.flush()

    if prepared_attachment is not None and attachment_object_key is not None:
        try:
            await attachment_store.put(attachment_object_key, prepared_attachment.content)
        except AttachmentStorageError as exc:
            async with session.begin():
                failed_report = await session.scalar(
                    select(Report).where(Report.id == report.id).with_for_update()
                )
                if failed_report is not None:
                    await session.delete(failed_report)
            try:
                await attachment_store.delete(attachment_object_key)
            except AttachmentStorageError:
                pass
            raise ServiceError(
                503, "ATTACHMENT_STORAGE_UNAVAILABLE", "이미지를 안전하게 저장하지 못했습니다."
            ) from exc

    try:
        async with asyncio.timeout(settings.ai_timeout_seconds):
            extraction = await extractor.extract(scan.masked_text)
        validate_evidence_quotes(extraction, scan.masked_text)
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            safe_error_code = "TIMEOUT"
        elif isinstance(exc, (ValidationError, ValueError)):
            safe_error_code = "INVALID_SCHEMA"
        else:
            safe_error_code = "PROVIDER_UNAVAILABLE"

        async with session.begin():
            stored_report = await session.scalar(
                select(Report).where(Report.id == report.id).with_for_update()
            )
            stored_analysis = await session.scalar(
                select(ReportAnalysis).where(ReportAnalysis.id == analysis.id).with_for_update()
            )
            if stored_report is None or stored_analysis is None:
                raise ServiceError(
                    503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다."
                ) from exc
            if stored_analysis.status == AnalysisStatus.PENDING.value:
                stored_report.status = ReportStatus.ANALYSIS_FAILED.value
                stored_analysis.status = AnalysisStatus.FAILED.value
                stored_analysis.safe_error_code = safe_error_code
                stored_analysis.completed_at = datetime.now(UTC)
        return _analysis_response(stored_report, stored_analysis)

    async with session.begin():
        stored_report = await session.scalar(
            select(Report).where(Report.id == report.id).with_for_update()
        )
        stored_analysis = await session.scalar(
            select(ReportAnalysis).where(ReportAnalysis.id == analysis.id).with_for_update()
        )
        if stored_report is None or stored_analysis is None:
            raise ServiceError(503, "ANALYSIS_UNAVAILABLE", "분석 결과를 사용할 수 없습니다.")
        if stored_analysis.status == AnalysisStatus.PENDING.value:
            stored_report.status = ReportStatus.AWAITING_CONFIRMATION.value
            stored_analysis.schema_version = extraction.schema_version
            stored_analysis.taxonomy_version = extraction.taxonomy_version
            stored_analysis.adapter_name = extraction.adapter_name
            stored_analysis.model_id = extraction.model_id
            stored_analysis.status = AnalysisStatus.SUCCEEDED.value
            stored_analysis.technical_candidate = extraction.technical.model_dump(mode="json")
            stored_analysis.consultation_candidate = extraction.consultation.model_dump(mode="json")
            stored_analysis.completed_at = datetime.now(UTC)
    return _analysis_response(stored_report, stored_analysis)


async def include_attachment_preview(
    session: AsyncSession,
    principal_digest: bytes,
    response: ReportAnalysisResponse,
    attachment_store: LocalAttachmentStore,
) -> ReportAnalysisResponse:
    if not isinstance(response.root, ReportAnalysisConfirmationResponse):
        return response

    attachment = await session.scalar(
        select(Attachment)
        .join(Report)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(
            ReportAnalysis.id == response.root.analysis_id,
            Report.session_digest == principal_digest,
        )
    )
    if attachment is None:
        return response
    try:
        content = await attachment_store.read(attachment.object_key)
    except AttachmentStorageError as exc:
        raise ServiceError(
            503, "ATTACHMENT_STORAGE_UNAVAILABLE", "이미지를 불러오지 못했습니다."
        ) from exc
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), attachment.content_sha256):
        raise ServiceError(503, "ATTACHMENT_INTEGRITY_ERROR", "이미지를 불러오지 못했습니다.")
    response.root.attachment = AttachmentResponse(
        id=attachment.id,
        url=attachment_data_url(attachment.content_type, content),
    )
    return response


async def confirm_report(
    session: AsyncSession,
    principal_digest: bytes,
    request: ReportConfirmationRequest,
    settings: Settings,
) -> ReportConfirmedResponse:
    payload_sha256 = _payload_sha256(request)
    reference_key = settings.reference_hmac_key.get_secret_value().encode()
    now = datetime.now(UTC)

    try:
        ensure_confirmation_strings_are_safe(
            request.technical.symptom,
            request.technical.error_code,
            request.consultation.symbol_name,
            request.consultation.symbol_code,
        )
    except SensitiveInputError as exc:
        raise ServiceError(
            422, "SENSITIVE_CONFIRMATION", "확인값에 민감정보를 넣을 수 없습니다."
        ) from exc

    async with session.begin():
        await _lock_idempotency_key(
            session, principal_digest, "CONFIRM_REPORT", request.client_request_id
        )
        report = await session.scalar(
            select(Report)
            .join(ReportAnalysis)
            .where(ReportAnalysis.id == request.analysis_id)
            .options(
                selectinload(Report.consultation_card),
                selectinload(Report.attachment),
            )
            .with_for_update()
        )
        if report is None or not hmac.compare_digest(report.session_digest, principal_digest):
            raise ServiceError(404, "ANALYSIS_NOT_FOUND", "분석 결과를 찾을 수 없습니다.")

        analysis = await session.scalar(
            select(ReportAnalysis).where(ReportAnalysis.id == request.analysis_id)
        )
        latest_version = await session.scalar(
            select(func.max(ReportAnalysis.version)).where(ReportAnalysis.report_id == report.id)
        )
        if (
            analysis is None
            or analysis.version != request.analysis_version
            or analysis.version != latest_version
        ):
            raise ServiceError(409, "STALE_ANALYSIS", "최신 분석 결과를 다시 확인해 주세요.")
        if analysis.status != AnalysisStatus.SUCCEEDED.value:
            raise ServiceError(409, "ANALYSIS_NOT_READY", "완료된 분석만 확인할 수 있습니다.")
        if request.masked_text != report.masked_text:
            raise ServiceError(409, "MASKED_TEXT_CHANGED", "마스킹된 제보가 변경되었습니다.")
        stored_attachment_id = report.attachment.id if report.attachment is not None else None
        if request.attachment_id != stored_attachment_id:
            raise ServiceError(409, "ATTACHMENT_CHANGED", "첨부 이미지를 다시 확인해 주세요.")

        reference_number = make_reference_number(
            principal_digest,
            analysis.id.bytes,
            request.client_request_id.bytes,
            reference_key,
        )
        card = report.consultation_card
        if report.status == ReportStatus.CONFIRMED.value and card is not None:
            if card.confirmation_request_id != request.client_request_id or not hmac.compare_digest(
                card.confirmation_payload_sha256 or "", payload_sha256
            ):
                raise ServiceError(
                    409, "REPORT_ALREADY_CONFIRMED", "이미 확인이 완료된 제보입니다."
                )
            if card.expires_at is None:
                raise ServiceError(503, "CARD_UNAVAILABLE", "상담카드를 사용할 수 없습니다.")
            return ReportConfirmedResponse(
                consultation_card=ConsultationCardIssued(
                    reference_number=reference_number,
                    expires_at=card.expires_at,
                )
            )
        if report.status != ReportStatus.AWAITING_CONFIRMATION.value:
            raise ServiceError(409, "INVALID_REPORT_STATE", "확인할 수 없는 제보 상태입니다.")

        confirmed_at = now
        report.status = ReportStatus.CONFIRMED.value
        report.confirmed_at = confirmed_at
        technical = TechnicalSymptom(
            report=report,
            taxonomy_version=analysis.taxonomy_version,
            channel=TechnicalChannel.MABLE.value,
            feature_area=FeatureArea.DOMESTIC_STOCK_ORDER.value,
            issue_type=request.technical.issue_type.value,
            symptom=request.technical.symptom,
            submission_status=request.technical.submission_status.value,
            error_code=request.technical.error_code,
            reported_occurred_at=request.technical.reported_occurred_at,
            confirmed_at=confirmed_at,
        )
        expires_at = now + CARD_TTL
        card = ConsultationCard(
            report=report,
            action=request.consultation.action.value,
            symbol_name=request.consultation.symbol_name,
            symbol_code=request.consultation.symbol_code,
            quantity=request.consultation.quantity,
            order_type=request.consultation.order_type.value,
            price_krw=request.consultation.price_krw,
            attempted_at=request.consultation.attempted_at,
            reference_digest=reference_digest(reference_number, reference_key),
            expires_at=expires_at,
            confirmation_request_id=request.client_request_id,
            confirmation_payload_sha256=payload_sha256,
        )
        session.add_all((technical, card))
        await session.flush()
        return ReportConfirmedResponse(
            consultation_card=ConsultationCardIssued(
                reference_number=reference_number,
                expires_at=expires_at,
            )
        )


async def delete_report(
    session: AsyncSession,
    principal_digest: bytes,
    request: DeleteConsultationCardRequest,
    settings: Settings,
) -> str | None:
    payload_sha256 = _payload_sha256(request)
    operation = "DELETE_REPORT"
    reference_key = settings.reference_hmac_key.get_secret_value().encode()

    async with session.begin():
        await _lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.principal_digest == principal_digest,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.client_request_id == request.client_request_id,
            )
        )
        if replay is not None:
            if not hmac.compare_digest(replay.payload_sha256, payload_sha256):
                raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 ID의 내용이 다릅니다.")
            return replay.attachment_object_key

        digest = reference_digest(request.reference_number, reference_key)
        report = await session.scalar(
            select(Report)
            .join(ConsultationCard)
            .where(ConsultationCard.reference_digest == digest)
            .options(selectinload(Report.attachment))
            .with_for_update()
        )
        if report is None or not hmac.compare_digest(report.session_digest, principal_digest):
            raise ServiceError(404, "CARD_NOT_FOUND", "상담카드를 찾을 수 없습니다.")

        attachment_object_key = (
            report.attachment.object_key if report.attachment is not None else None
        )
        session.add_all(
            (
                IdempotencyRecord(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=payload_sha256,
                    response_status=204,
                    attachment_object_key=attachment_object_key,
                ),
                AuditLog(
                    actor_type="customer_session",
                    action="REPORT_DELETED",
                    resource_fingerprint=hashlib.sha256(report.id.bytes).hexdigest(),
                ),
            )
        )
        await session.delete(report)
        return attachment_object_key


async def discard_report(
    session: AsyncSession,
    principal_digest: bytes,
    request: DiscardReportRequest,
) -> str | None:
    payload_sha256 = _payload_sha256(request)
    operation = "DISCARD_REPORT"

    async with session.begin():
        await _lock_idempotency_key(session, principal_digest, operation, request.client_request_id)
        replay = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.principal_digest == principal_digest,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.client_request_id == request.client_request_id,
            )
        )
        if replay is not None:
            if not hmac.compare_digest(replay.payload_sha256, payload_sha256):
                raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 ID의 내용이 다릅니다.")
            return replay.attachment_object_key

        report = await session.scalar(
            select(Report)
            .join(ReportAnalysis)
            .where(ReportAnalysis.id == request.analysis_id)
            .options(selectinload(Report.attachment))
            .with_for_update()
        )
        if report is None or not hmac.compare_digest(report.session_digest, principal_digest):
            raise ServiceError(404, "ANALYSIS_NOT_FOUND", "분석 결과를 찾을 수 없습니다.")
        if report.status != ReportStatus.AWAITING_CONFIRMATION.value:
            raise ServiceError(409, "REPORT_ALREADY_CONFIRMED", "이미 확인이 완료된 제보입니다.")

        attachment_object_key = (
            report.attachment.object_key if report.attachment is not None else None
        )
        session.add_all(
            (
                IdempotencyRecord(
                    principal_digest=principal_digest,
                    operation=operation,
                    client_request_id=request.client_request_id,
                    payload_sha256=payload_sha256,
                    response_status=204,
                    attachment_object_key=attachment_object_key,
                ),
                AuditLog(
                    actor_type="customer_session",
                    action="REPORT_DISCARDED",
                    resource_fingerprint=hashlib.sha256(report.id.bytes).hexdigest(),
                ),
            )
        )
        await session.delete(report)
        return attachment_object_key
