import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, PngImagePlugin
from sqlalchemy import delete, func, select

from app.ai import FakeDualExtractor, get_dual_extractor
from app.attachments import AttachmentStorageError, LocalAttachmentStore, get_attachment_store
from app.codes import AnalysisStatus
from app.config import Settings, get_settings
from app.db import engine, session_factory
from app.main import app
from app.models import (
    Attachment,
    AuditLog,
    ConsultationCard,
    IdempotencyRecord,
    ObjectDeletionJob,
    Report,
    ReportAnalysis,
    TechnicalSymptom,
)
from app.schemas import ExtractionResult

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires migrated PostgreSQL",
)


def _token(seed: int) -> str:
    return base64.urlsafe_b64encode(bytes([seed]) * 32).decode().rstrip("=")


def _headers(seed: int = 1) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(seed)}"}


class BlockingExtractor(FakeDualExtractor):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(self, masked_text: str) -> ExtractionResult:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().extract(masked_text)


class TimeoutExtractor(FakeDualExtractor):
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, masked_text: str) -> ExtractionResult:
        self.calls += 1
        raise TimeoutError


class SlowExtractor(FakeDualExtractor):
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, masked_text: str) -> ExtractionResult:
        self.calls += 1
        await asyncio.sleep(1)
        return await super().extract(masked_text)


class FailingExtractor(FakeDualExtractor):
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def extract(self, masked_text: str) -> ExtractionResult:
        self.calls += 1
        raise self.error


class CapturingExtractor(FakeDualExtractor):
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def extract(self, masked_text: str) -> ExtractionResult:
        self.inputs.append(masked_text)
        return await super().extract(masked_text)


class FailingAttachmentStore(LocalAttachmentStore):
    async def put(self, object_key: str, content: bytes) -> None:
        raise AttachmentStorageError("synthetic storage failure")


class FailingDeleteOnceAttachmentStore(LocalAttachmentStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.delete_calls = 0

    async def delete(self, object_key: str) -> None:
        self.delete_calls += 1
        if self.delete_calls == 1:
            raise AttachmentStorageError("synthetic deletion failure")
        await super().delete(object_key)


async def _clean_business_data() -> None:
    async with session_factory() as session, session.begin():
        await session.execute(delete(Report))
        await session.execute(delete(IdempotencyRecord))
        await session.execute(delete(AuditLog))
        await session.execute(delete(ObjectDeletionJob))


@pytest.fixture(autouse=True)
async def clean_business_data(tmp_path: Path) -> AsyncIterator[None]:
    store = LocalAttachmentStore(tmp_path / "attachments")
    app.dependency_overrides[get_attachment_store] = lambda: store
    await _clean_business_data()
    yield
    await _clean_business_data()
    await engine.dispose()
    app.dependency_overrides.pop(get_attachment_store, None)


def _screenshot_bytes() -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("account", "123-456-789")
    Image.new("RGB", (16, 12), "navy").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


async def test_customer_report_lifecycle_is_persisted_idempotent_and_deletable() -> None:
    transport = ASGITransport(app=app)
    analyze_id = str(uuid4())
    report_text = "주문 버튼 이후 계속 로딩됩니다. 연락처는 010-1234-5678입니다."

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        analyzed = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={"text": report_text, "client_request_id": analyze_id},
        )
        assert analyzed.status_code == 200
        assert analyzed.headers["cache-control"] == "no-store"
        analysis = analyzed.json()
        assert analysis["status"] == "confirmation"
        assert "010-1234-5678" not in analyzed.text
        assert "[PHONE]" in analysis["masked_text"]
        assert analysis["masked_items"] == ["PHONE"]

        replay = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={"text": report_text, "client_request_id": analyze_id},
        )
        assert replay.status_code == 200
        assert replay.json() == analysis

        conflict = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={
                "text": "다른 합성 제보이며 주문 결과를 확인하지 못한 상황입니다.",
                "client_request_id": analyze_id,
            },
        )
        assert conflict.status_code == 409

        confirmation_id = str(uuid4())
        confirmation = {
            "analysis_id": analysis["analysis_id"],
            "analysis_version": analysis["analysis_version"],
            "attachment_id": None,
            "masked_text": analysis["masked_text"],
            "technical": {
                "issue_type": "ORDER_SUBMISSION_FAILURE",
                "symptom": "주문 버튼 이후 지속 로딩",
                "submission_status": "UNKNOWN",
                "error_code": None,
                "reported_occurred_at": "2026-08-18T00:03:00Z",
            },
            "consultation": {
                "action": "SELL",
                "symbol_name": "삼성전자",
                "symbol_code": "005930",
                "quantity": 20,
                "order_type": "LIMIT",
                "price_krw": 70000,
                "attempted_at": "2026-08-18T00:03:00Z",
            },
            "client_request_id": confirmation_id,
        }
        wrong_owner = await client.post("/api/reports", headers=_headers(2), json=confirmation)
        assert wrong_owner.status_code == 404

        confirmed = await client.post("/api/reports", headers=_headers(), json=confirmation)
        assert confirmed.status_code == 200
        card = confirmed.json()["consultation_card"]
        assert card["reference_number"].startswith("KBSOS-")
        assert datetime.fromisoformat(card["expires_at"].replace("Z", "+00:00")) > datetime.now(UTC)

        confirmed_replay = await client.post("/api/reports", headers=_headers(), json=confirmation)
        assert confirmed_replay.status_code == 200
        assert confirmed_replay.json() == confirmed.json()

        async with session_factory() as session:
            stored_report = await session.scalar(select(Report))
            stored_technical = await session.scalar(select(TechnicalSymptom))
            stored_card = await session.scalar(select(ConsultationCard))
            assert stored_report is not None and report_text not in stored_report.masked_text
            assert stored_technical is not None and not hasattr(stored_technical, "symbol_code")
            assert stored_card is not None and len(stored_card.reference_digest or b"") == 32
            assert not hasattr(stored_card, "reference_number")

        delete_id = str(uuid4())
        deletion = {
            "reference_number": card["reference_number"],
            "client_request_id": delete_id,
        }
        deleted, concurrent_replay = await asyncio.gather(
            client.request("DELETE", "/api/consultation-cards", headers=_headers(), json=deletion),
            client.request("DELETE", "/api/consultation-cards", headers=_headers(), json=deletion),
        )
        assert {deleted.status_code, concurrent_replay.status_code} == {204}
        delete_replay = await client.request(
            "DELETE", "/api/consultation-cards", headers=_headers(), json=deletion
        )
        assert delete_replay.status_code == 204

        delete_conflict = await client.request(
            "DELETE",
            "/api/consultation-cards",
            headers=_headers(),
            json={
                "reference_number": "KBSOS-AAAAAAAAAAAAAAAAAAAAAAAAAA",
                "client_request_id": delete_id,
            },
        )
        assert delete_conflict.status_code == 409

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
        assert await session.scalar(select(func.count()).select_from(TechnicalSymptom)) == 0
        assert await session.scalar(select(func.count()).select_from(ConsultationCard)) == 0
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1


async def test_localized_placeholders_are_canonical_in_db_ai_and_response() -> None:
    extractor = CapturingExtractor()
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            analyzed = await client.post(
                "/api/reports/analyze",
                headers=_headers(),
                json={
                    "text": (
                        "주문 오류 제보이며 [전화번호], [계좌번호], [이메일]은 직접 가렸습니다."
                    ),
                    "client_request_id": str(uuid4()),
                },
            )
    finally:
        app.dependency_overrides.pop(get_dual_extractor, None)

    assert analyzed.status_code == 200
    payload = analyzed.json()
    canonical = "주문 오류 제보이며 [PHONE], [ACCOUNT], [EMAIL]은 직접 가렸습니다."
    assert payload["masked_text"] == canonical
    assert payload["masked_items"] == ["PHONE", "ACCOUNT", "EMAIL"]
    assert extractor.inputs == [canonical]
    assert all(value not in analyzed.text for value in ("[전화번호]", "[계좌번호]", "[이메일]"))
    async with session_factory() as session:
        stored = await session.scalar(select(Report))
        assert stored is not None
        assert stored.masked_text == canonical


@pytest.mark.parametrize("action", ["BUY", "SELL", "UNKNOWN"])
async def test_all_order_actions_are_saved_through_the_api(action: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        analyzed = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={
                "text": "주문 버튼 이후 계속 로딩되어 결과를 확인하지 못한 합성 제보입니다.",
                "client_request_id": str(uuid4()),
            },
        )
        analysis = analyzed.json()
        confirmed = await client.post(
            "/api/reports",
            headers=_headers(),
            json={
                "analysis_id": analysis["analysis_id"],
                "analysis_version": analysis["analysis_version"],
                "attachment_id": None,
                "masked_text": analysis["masked_text"],
                "technical": {
                    "issue_type": "UNKNOWN",
                    "symptom": None,
                    "submission_status": "UNKNOWN",
                    "error_code": None,
                    "reported_occurred_at": None,
                },
                "consultation": {
                    "action": action,
                    "symbol_name": None,
                    "symbol_code": None,
                    "quantity": None,
                    "order_type": "UNKNOWN",
                    "price_krw": None,
                    "attempted_at": None,
                },
                "client_request_id": str(uuid4()),
            },
        )

    assert analyzed.status_code == 200
    assert confirmed.status_code == 200
    async with session_factory() as session:
        card = await session.scalar(select(ConsultationCard))
        assert card is not None
        assert card.action == action


async def test_owner_and_sensitive_input_boundaries_do_not_leak_values() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={
                "text": "주문 오류가 발생했고 OTP는 123456입니다. 저장하면 안 됩니다.",
                "client_request_id": str(uuid4()),
            },
        )
        assert rejected.status_code == 422
        assert "123456" not in rejected.text

        invalid = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={
                "text": "비밀 입력값이 응답에 노출되면 안 되는 합성 제보 문장입니다.",
                "client_request_id": "not-a-uuid-secret",
            },
        )
        assert invalid.status_code == 422
        assert "not-a-uuid-secret" not in invalid.text

        oversized_secret = "do-not-echo-this-value" * 1000
        oversized = await client.post(
            "/api/reports/analyze",
            headers={**_headers(), "Content-Type": "application/json"},
            content=json.dumps(
                {
                    "text": "주문 화면이 계속 로딩되는 합성 제보 문장입니다.",
                    "client_request_id": str(uuid4()),
                    "extra": oversized_secret,
                }
            ),
        )
        assert oversized.status_code == 413
        assert oversized_secret not in oversized.text

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0


async def test_concurrent_analyze_retries_create_one_report() -> None:
    transport = ASGITransport(app=app)
    extractor = BlockingExtractor()
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    request_id = str(uuid4())
    payload = {
        "text": "동시에 재시도해도 제보 한 건만 저장되어야 하는 합성 문장입니다.",
        "client_request_id": request_id,
    }
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first_task = asyncio.create_task(
                client.post("/api/reports/analyze", headers=_headers(), json=payload)
            )
            await asyncio.wait_for(extractor.entered.wait(), timeout=2)
            second = await client.post("/api/reports/analyze", headers=_headers(), json=payload)
            extractor.release.set()
            first = await first_task
    finally:
        app.dependency_overrides.pop(get_dual_extractor, None)

    assert first.status_code == second.status_code == 200
    assert {first.json()["status"], second.json()["status"]} == {"confirmation", "pending"}
    assert extractor.calls == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 1


async def test_failed_analysis_is_purged_and_not_reinvoked() -> None:
    transport = ASGITransport(app=app)
    extractor = TimeoutExtractor()
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    request_id = str(uuid4())
    payload = {
        "text": "AI 시간초과 상태를 안전하게 저장하는 합성 제보 문장입니다.",
        "client_request_id": request_id,
    }
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post("/api/reports/analyze", headers=_headers(), json=payload)
            replay = await client.post("/api/reports/analyze", headers=_headers(), json=payload)
            conflict = await client.post(
                "/api/reports/analyze",
                headers=_headers(),
                json={
                    "text": "동일한 요청 ID에 다른 payload를 사용한 합성 제보입니다.",
                    "client_request_id": request_id,
                },
            )
            new_request = await client.post(
                "/api/reports/analyze",
                headers=_headers(),
                json={**payload, "client_request_id": str(uuid4())},
            )
    finally:
        app.dependency_overrides.pop(get_dual_extractor, None)

    assert failed.status_code == replay.status_code == 200
    assert failed.json() == replay.json()
    assert failed.json()["status"] == "failed"
    assert failed.json()["error"] == {"code": "TIMEOUT"}
    assert conflict.status_code == 409
    assert new_request.status_code == 200
    assert new_request.json()["status"] == "failed"
    assert extractor.calls == 2
    async with session_factory() as session:
        analysis = await session.scalar(select(ReportAnalysis))
        report = await session.scalar(select(Report))
        idempotency = await session.scalar(select(IdempotencyRecord))
        assert analysis is None
        assert report is None
        assert idempotency is not None
        assert idempotency.safe_failure_code == "TIMEOUT"
        assert not hasattr(idempotency, "attachment_object_key")


async def test_backend_timeout_covers_the_complete_adapter_call() -> None:
    extractor = SlowExtractor()
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    app.dependency_overrides[get_settings] = lambda: Settings(ai_timeout_seconds=0.01)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post(
                "/api/reports/analyze",
                headers=_headers(),
                json={
                    "text": "AI 전체 호출 제한을 확인하기 위한 합성 제보 문장입니다.",
                    "client_request_id": str(uuid4()),
                },
            )
    finally:
        app.dependency_overrides.pop(get_dual_extractor, None)
        app.dependency_overrides.pop(get_settings, None)

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["error"] == {"code": "TIMEOUT"}
    assert extractor.calls == 1


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ValueError("invalid extraction schema"), "INVALID_SCHEMA"),
        (RuntimeError("provider unavailable"), "PROVIDER_UNAVAILABLE"),
    ],
)
async def test_analysis_adapter_failures_use_safe_persisted_codes(
    error: Exception, expected_code: str
) -> None:
    transport = ASGITransport(app=app)
    extractor = FailingExtractor(error)
    payload = {
        "text": "AI 연동 실패 상태를 안전하게 저장하기 위한 기술 증상 테스트 문장입니다.",
        "client_request_id": str(uuid4()),
    }
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post("/api/reports/analyze", headers=_headers(), json=payload)
            replay = await client.post("/api/reports/analyze", headers=_headers(), json=payload)
    finally:
        app.dependency_overrides.pop(get_dual_extractor, None)

    assert failed.status_code == replay.status_code == 200
    assert failed.json() == replay.json()
    assert failed.json() == {
        "analysis_id": failed.json()["analysis_id"],
        "analysis_version": 1,
        "status": "failed",
        "error": {"code": expected_code},
    }
    assert extractor.calls == 1

    async with session_factory() as session:
        analysis = await session.scalar(select(ReportAnalysis))
        idempotency = await session.scalar(select(IdempotencyRecord))
        assert analysis is None
        assert idempotency is not None
        assert idempotency.safe_failure_code == expected_code


async def test_only_latest_succeeded_analysis_can_be_confirmed() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        analyzed = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={
                "text": "최신 분석 버전만 확정할 수 있어야 하는 합성 제보 문장입니다.",
                "client_request_id": str(uuid4()),
            },
        )
        assert analyzed.status_code == 200
        first = analyzed.json()

        async with session_factory() as session, session.begin():
            stored = await session.scalar(
                select(ReportAnalysis).where(ReportAnalysis.id == first["analysis_id"])
            )
            assert stored is not None
            latest = ReportAnalysis(
                report_id=stored.report_id,
                version=stored.version + 1,
                schema_version=stored.schema_version,
                taxonomy_version=stored.taxonomy_version,
                adapter_name=stored.adapter_name,
                model_id=stored.model_id,
                status=AnalysisStatus.SUCCEEDED.value,
                technical_candidate=stored.technical_candidate,
                consultation_candidate=stored.consultation_candidate,
                completed_at=datetime.now(UTC),
            )
            session.add(latest)
            await session.flush()
            latest_id = str(latest.id)

        confirmation = {
            "analysis_id": first["analysis_id"],
            "analysis_version": first["analysis_version"],
            "attachment_id": None,
            "masked_text": first["masked_text"],
            "technical": {
                "issue_type": "UNKNOWN",
                "symptom": None,
                "submission_status": "UNKNOWN",
                "error_code": None,
                "reported_occurred_at": None,
            },
            "consultation": {
                "action": "UNKNOWN",
                "symbol_name": None,
                "symbol_code": None,
                "quantity": None,
                "order_type": "UNKNOWN",
                "price_krw": None,
                "attempted_at": None,
            },
            "client_request_id": str(uuid4()),
        }
        stale = await client.post("/api/reports", headers=_headers(), json=confirmation)
        assert stale.status_code == 409
        assert stale.json()["code"] == "STALE_ANALYSIS"

        confirmation["analysis_id"] = latest_id
        confirmation["analysis_version"] = 2
        confirmation["client_request_id"] = str(uuid4())
        current = await client.post("/api/reports", headers=_headers(), json=confirmation)
        assert current.status_code == 200


async def test_abandoned_analysis_is_discarded_idempotently() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        analyzed = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            json={
                "text": "분석 결과를 확인한 뒤 저장하지 않고 다시 작성하는 합성 제보입니다.",
                "client_request_id": str(uuid4()),
            },
        )
        assert analyzed.status_code == 200
        analysis_id = analyzed.json()["analysis_id"]
        discard_id = str(uuid4())
        deletion = {"analysis_id": analysis_id, "client_request_id": discard_id}

        wrong_owner = await client.request(
            "DELETE", "/api/reports", headers=_headers(2), json=deletion
        )
        assert wrong_owner.status_code == 404

        discarded = await client.request(
            "DELETE", "/api/reports", headers=_headers(), json=deletion
        )
        assert discarded.status_code == 204
        replay = await client.request("DELETE", "/api/reports", headers=_headers(), json=deletion)
        assert replay.status_code == 204

        conflict = await client.request(
            "DELETE",
            "/api/reports",
            headers=_headers(),
            json={"analysis_id": str(uuid4()), "client_request_id": discard_id},
        )
        assert conflict.status_code == 409

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1


async def test_screenshot_is_sanitized_confirmed_and_deleted(tmp_path: Path) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        analyzed = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            data={
                "text": "주문 화면에서 버튼을 누른 뒤 계속 로딩되는 오류 상황입니다.",
                "client_request_id": str(uuid4()),
                "screenshot_redacted_confirmed": "true",
            },
            files={"screenshot": ("error.png", _screenshot_bytes(), "image/png")},
        )
        assert analyzed.status_code == 200
        analysis = analyzed.json()
        assert analysis["status"] == "confirmation"
        assert analysis["attachment"]["url"].startswith("data:image/png;base64,")

        async with session_factory() as session:
            attachment = await session.scalar(select(Attachment))
            assert attachment is not None
            stored = (tmp_path / "attachments" / attachment.object_key).read_bytes()
            assert b"123-456-789" not in stored
            assert attachment.byte_size == len(stored)

        confirmation = {
            "analysis_id": analysis["analysis_id"],
            "analysis_version": analysis["analysis_version"],
            "attachment_id": str(uuid4()),
            "masked_text": analysis["masked_text"],
            "technical": {
                "issue_type": "UNKNOWN",
                "symptom": None,
                "submission_status": "UNKNOWN",
                "error_code": None,
                "reported_occurred_at": None,
            },
            "consultation": {
                "action": "UNKNOWN",
                "symbol_name": None,
                "symbol_code": None,
                "quantity": None,
                "order_type": "UNKNOWN",
                "price_krw": None,
                "attempted_at": None,
            },
            "client_request_id": str(uuid4()),
        }
        mismatch = await client.post("/api/reports", headers=_headers(), json=confirmation)
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "ATTACHMENT_CHANGED"

        confirmation["attachment_id"] = analysis["attachment"]["id"]
        confirmation["client_request_id"] = str(uuid4())
        confirmed = await client.post("/api/reports", headers=_headers(), json=confirmation)
        assert confirmed.status_code == 200

        deleted = await client.request(
            "DELETE",
            "/api/consultation-cards",
            headers=_headers(),
            json={
                "reference_number": confirmed.json()["consultation_card"]["reference_number"],
                "client_request_id": str(uuid4()),
            },
        )
        assert deleted.status_code == 204
        assert not (tmp_path / "attachments" / attachment.object_key).exists()

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Attachment)) == 0


async def test_screenshot_endpoint_rejects_invalid_file_without_leaking_it() -> None:
    secret = b"not-an-image-with-account-123-456-789"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            data={
                "text": "주문 화면에서 버튼을 누른 뒤 계속 로딩되는 오류 상황입니다.",
                "client_request_id": str(uuid4()),
                "screenshot_redacted_confirmed": "true",
            },
            files={"screenshot": ("error.png", secret, "image/png")},
        )

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_ATTACHMENT"
    assert secret.decode() not in invalid.text


async def test_screenshot_storage_failure_is_safe_and_rolls_back_report(tmp_path: Path) -> None:
    app.dependency_overrides[get_attachment_store] = lambda: FailingAttachmentStore(
        tmp_path / "failing-attachments"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        failed = await client.post(
            "/api/reports/analyze",
            headers=_headers(),
            data={
                "text": "주문 화면에서 버튼을 누른 뒤 계속 로딩되는 오류 상황입니다.",
                "client_request_id": str(uuid4()),
                "screenshot_redacted_confirmed": "true",
            },
            files={"screenshot": ("error.png", _screenshot_bytes(), "image/png")},
        )

    assert failed.status_code == 503
    assert failed.json()["code"] == "ATTACHMENT_STORAGE_UNAVAILABLE"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
        assert await session.scalar(select(func.count()).select_from(Attachment)) == 0


async def test_ai_failure_removes_image_metadata_and_keeps_safe_deletion_retry(
    tmp_path: Path,
) -> None:
    store = FailingDeleteOnceAttachmentStore(tmp_path / "retry-attachments")
    extractor = FailingExtractor(RuntimeError("provider unavailable with internal details"))
    app.dependency_overrides[get_attachment_store] = lambda: store
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post(
                "/api/reports/analyze",
                headers=_headers(),
                data={
                    "text": "주문 화면 오류이며 연락처 010-1234-5678은 저장하면 안 됩니다.",
                    "client_request_id": str(uuid4()),
                    "screenshot_redacted_confirmed": "true",
                },
                files={
                    "screenshot": ("secret-original-name.png", _screenshot_bytes(), "image/png")
                },
            )
    finally:
        app.dependency_overrides.pop(get_attachment_store, None)
        app.dependency_overrides.pop(get_dual_extractor, None)

    assert failed.status_code == 200
    assert failed.json()["error"] == {"code": "PROVIDER_UNAVAILABLE"}
    assert "010-1234-5678" not in failed.text
    assert "secret-original-name.png" not in failed.text
    assert "internal details" not in failed.text
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
        assert await session.scalar(select(func.count()).select_from(ReportAnalysis)) == 0
        assert await session.scalar(select(func.count()).select_from(Attachment)) == 0
        job = await session.scalar(select(ObjectDeletionJob))
        assert job is not None
        assert job.status == "RETRY_PENDING"
        assert job.safe_error_code == "STORAGE_UNAVAILABLE"
        assert (store.root / job.object_key).exists()
        assert job.next_attempt_at is not None
        retry_at = job.next_attempt_at
        job_id = job.id
        object_key = job.object_key
        await session.rollback()

        from app.services.lifecycle import process_object_deletion_jobs

        retry = await process_object_deletion_jobs(
            session,
            store,
            now=retry_at,
            batch_size=1,
            job_ids=(job_id,),
        )
        assert retry.succeeded == 1
        assert not (store.root / object_key).exists()


@pytest.mark.parametrize("confirmation", [None, "false", "yes", "1", "TRUE"])
async def test_screenshot_requires_explicit_redaction_confirmation(
    confirmation: str | None,
) -> None:
    extractor = CapturingExtractor()
    app.dependency_overrides[get_dual_extractor] = lambda: extractor
    data = {
        "text": "주문 화면에서 버튼을 누른 뒤 계속 로딩되는 오류 상황입니다.",
        "client_request_id": str(uuid4()),
    }
    if confirmation is not None:
        data["screenshot_redacted_confirmed"] = confirmation
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.post(
                "/api/reports/analyze",
                headers=_headers(),
                data=data,
                files={"screenshot": ("error.png", _screenshot_bytes(), "image/png")},
            )
    finally:
        app.dependency_overrides.pop(get_dual_extractor, None)

    assert rejected.status_code == 422
    assert rejected.headers["content-type"].startswith("application/problem+json")
    assert rejected.json()["code"] == "SCREENSHOT_REDACTION_REQUIRED"
    assert extractor.inputs == []
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 0
        assert await session.scalar(select(func.count()).select_from(Attachment)) == 0
