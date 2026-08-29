import asyncio
import os
from argparse import Namespace
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update

from app.api.dependencies import get_clock, get_security_sleeper
from app.attachments import LocalAttachmentStore
from app.codes import AgentRole, ReportStatus
from app.config import Settings, get_settings
from app.db import engine, session_factory
from app.main import app
from app.models import (
    AgentAccessToken,
    AgentAccount,
    AgentVerification,
    Attachment,
    AuditLog,
    ConsultationCard,
    IdempotencyRecord,
    ObjectDeletionJob,
    PolicySnapshot,
    RateLimitBucket,
    Report,
    Symbol,
    SymbolMasterVersion,
    TechnicalSymptom,
)
from app.security import (
    hash_password,
    make_opaque_token,
    opaque_token_digest,
    reference_digest,
    verify_password,
)
from app.services.lifecycle import purge_expired_data
from scripts.seed_agent import run as seed_agent

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires migrated PostgreSQL",
)

FIXED_NOW = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
REFERENCE = "KBSOS-" + "A" * 26
PASSWORD_HASH = hash_password("demo")
TEST_SETTINGS = Settings(
    app_env="test",
    session_hmac_key=SecretStr("s" * 32),
    reference_hmac_key=SecretStr("r" * 32),
    agent_token_hmac_key=SecretStr("t" * 32),
    rate_limit_hmac_key=SecretStr("l" * 32),
)


@dataclass(slots=True)
class AgentTestState:
    delays: list[float]
    agent_id: UUID
    operator_id: UUID


async def _clean() -> None:
    async with session_factory() as session, session.begin():
        await session.execute(delete(Report))
        await session.execute(delete(SymbolMasterVersion))
        await session.execute(delete(IdempotencyRecord))
        await session.execute(delete(AuditLog))
        await session.execute(delete(AgentAccessToken))
        await session.execute(delete(RateLimitBucket))
        await session.execute(delete(AgentVerification))
        await session.execute(delete(AgentAccount))
        await session.execute(delete(ObjectDeletionJob))


@pytest.fixture(autouse=True)
async def clean_agent_data() -> AsyncIterator[AgentTestState]:
    delays: list[float] = []

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    await _clean()
    async with session_factory() as session, session.begin():
        agent = AgentAccount(
            employee_id="CS1024",
            agent_label="데모 상담원",
            role=AgentRole.AGENT.value,
            password_hash=PASSWORD_HASH,
            is_active=True,
            created_at=FIXED_NOW,
            updated_at=FIXED_NOW,
        )
        operator = AgentAccount(
            employee_id="OP1024",
            agent_label="데모 운영자",
            role=AgentRole.OPERATOR.value,
            password_hash=PASSWORD_HASH,
            is_active=True,
            created_at=FIXED_NOW,
            updated_at=FIXED_NOW,
        )
        session.add_all((agent, operator))
        master = SymbolMasterVersion(
            version="agent-test-symbols",
            source_url="https://example.invalid/krx",
            source_as_of=FIXED_NOW.date(),
            source_sha256="8" * 64,
            source_encoding="UTF-8-SIG",
            schema_version="krx-all-symbols.v1",
            row_count=1,
            is_active=True,
        )
        master.symbols.append(
            Symbol(
                code="000001",
                name_ko="합성종목",
                market="KOSPI",
                source_market="KOSPI",
                stock_type="보통주",
            )
        )
        session.add(master)
        await session.flush()

    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    app.dependency_overrides[get_clock] = lambda: lambda: FIXED_NOW
    app.dependency_overrides[get_security_sleeper] = lambda: no_wait
    state = AgentTestState(delays=delays, agent_id=agent.id, operator_id=operator.id)
    yield state
    app.dependency_overrides.clear()
    await _clean()
    await engine.dispose()


async def _create_card(
    *,
    reference: str = REFERENCE,
    received_at: datetime | None = None,
    expires_at: datetime | None = None,
    purge_at: datetime | None = None,
    action: str = "SELL",
    order_type: str = "LIMIT",
    price_krw: int | None = 70_000,
    with_attachment: bool = False,
) -> tuple[UUID, UUID]:
    received_at = received_at or FIXED_NOW - timedelta(hours=1)
    expires_at = expires_at or FIXED_NOW + timedelta(hours=1)
    purge_at = purge_at or received_at + timedelta(hours=72)
    async with session_factory() as session, session.begin():
        symbol_master_version_id = await session.scalar(
            select(SymbolMasterVersion.id).where(SymbolMasterVersion.is_active.is_(True))
        )
        assert symbol_master_version_id is not None
        policy = await session.scalar(select(PolicySnapshot).limit(1))
        if policy is None:
            policy = PolicySnapshot(
                version="agent-test-policy",
                source_url="https://example.invalid/policy",
                source_checked_on=FIXED_NOW.date(),
                content={"schema_version": "test"},
                content_sha256="0" * 64,
                created_at=received_at,
            )
            session.add(policy)
            await session.flush()
        report = Report(
            session_digest=b"c" * 32,
            client_request_id=uuid4(),
            policy_snapshot_id=policy.id,
            pii_policy_version="pii-mask.v1",
            masked_text="합성 제보이며 개인정보와 실제 주문정보를 포함하지 않습니다.",
            request_payload_sha256="1" * 64,
            status=ReportStatus.CONFIRMED.value,
            received_at=received_at,
            purge_at=purge_at,
            confirmed_at=received_at,
            updated_at=received_at,
        )
        report.technical_symptom = TechnicalSymptom(
            taxonomy_version="test.v1",
            channel="MABLE",
            feature_area="DOMESTIC_STOCK_ORDER",
            issue_type="ORDER_SUBMISSION_FAILURE",
            symptom="주문 버튼 이후 화면 멈춤",
            submission_status="CUSTOMER_REPORTED_SUBMITTED",
            error_code=None,
            reported_occurred_at=None,
            confirmed_at=received_at,
        )
        report.consultation_card = card = ConsultationCard(
            action=action,
            symbol_name="합성종목",
            symbol_code="000001",
            symbol_master_version_id=symbol_master_version_id,
            quantity=3,
            order_type=order_type,
            price_krw=price_krw,
            attempted_at=None,
            reference_digest=reference_digest(
                reference,
                TEST_SETTINGS.reference_hmac_key.get_secret_value().encode(),
            ),
            expires_at=expires_at,
            confirmation_request_id=uuid4(),
            confirmation_payload_sha256="2" * 64,
            created_at=received_at,
            updated_at=received_at,
        )
        if with_attachment:
            report.attachment = Attachment(
                object_key="a" * 43,
                content_type="image/png",
                byte_size=16,
                width=2,
                height=2,
                content_sha256="3" * 64,
                created_at=received_at,
            )
        session.add(report)
        await session.flush()
        return card.id, report.id


async def _login(client: AsyncClient, employee_id: str = "CS1024", password: str = "demo") -> str:
    response = await client.post(
        "/api/auth/login",
        json={"employee_id": employee_id, "password": password},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _verification_payload(card_id: UUID, request_id: UUID | None = None) -> dict[str, object]:
    return {
        "card_id": str(card_id),
        "action": "SELL",
        "symbol_name": "합성종목",
        "symbol_code": "000001",
        "quantity": 3,
        "order_type": "LIMIT",
        "price_krw": 70_000,
        "submission_status": "CUSTOMER_REPORTED_SUBMITTED",
        "order_history_checked": True,
        "client_request_id": str(request_id or uuid4()),
    }


async def test_login_is_db_backed_opaque_and_indistinguishable_on_failure(
    clean_agent_data: AgentTestState,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        success = await client.post(
            "/api/auth/login", json={"employee_id": " cs1024 ", "password": "demo"}
        )
        wrong_id = await client.post(
            "/api/auth/login", json={"employee_id": "NO1024", "password": "demo"}
        )
        wrong_password = await client.post(
            "/api/auth/login", json={"employee_id": "CS1024", "password": "wrong"}
        )

    assert success.status_code == 200
    assert success.headers["cache-control"] == "no-store"
    body = success.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "AGENT"
    assert len(body["access_token"]) == 43
    assert wrong_id.status_code == wrong_password.status_code == 401
    assert wrong_id.json() == wrong_password.json()
    assert clean_agent_data.delays[-2:] == [0.3, 0.3]

    async with session_factory() as session:
        token_row = await session.scalar(select(AgentAccessToken))
        assert token_row is not None
        assert body["access_token"].encode() != token_row.token_digest
        account = await session.scalar(
            select(AgentAccount).where(AgentAccount.id == clean_agent_data.agent_id)
        )
        assert account is not None
        assert account.password_hash.startswith("$argon2")
        assert "demo" not in account.password_hash


async def test_disabled_expired_invalid_and_wrong_role_are_rejected(
    clean_agent_data: AgentTestState,
) -> None:
    expired_token = make_opaque_token()
    revoked_token = make_opaque_token()
    async with session_factory() as session, session.begin():
        session.add_all(
            (
                AgentAccessToken(
                    agent_id=clean_agent_data.agent_id,
                    token_digest=opaque_token_digest(
                        expired_token,
                        TEST_SETTINGS.agent_token_hmac_key.get_secret_value().encode(),
                    ),
                    created_at=FIXED_NOW - timedelta(minutes=30),
                    expires_at=FIXED_NOW,
                ),
                AgentAccessToken(
                    agent_id=clean_agent_data.operator_id,
                    token_digest=opaque_token_digest(
                        revoked_token,
                        TEST_SETTINGS.agent_token_hmac_key.get_secret_value().encode(),
                    ),
                    created_at=FIXED_NOW - timedelta(minutes=1),
                    expires_at=FIXED_NOW + timedelta(minutes=29),
                    revoked_at=FIXED_NOW,
                ),
            )
        )
        account = await session.get(AgentAccount, clean_agent_data.agent_id)
        assert account is not None
        account.is_active = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        disabled = await client.post(
            "/api/auth/login", json={"employee_id": "CS1024", "password": "demo"}
        )
        expired = await client.get("/api/agent/consultation-cards", headers=_auth(expired_token))
        revoked = await client.get("/api/agent/consultation-cards", headers=_auth(revoked_token))
        invalid = await client.get(
            "/api/agent/consultation-cards", headers=_auth(make_opaque_token())
        )
        operator_token = await _login(client, "OP1024")
        wrong_role = await client.get(
            "/api/agent/consultation-cards", headers=_auth(operator_token)
        )

    assert (
        disabled.status_code
        == expired.status_code
        == revoked.status_code
        == invalid.status_code
        == 401
    )
    assert disabled.json()["code"] == "INVALID_CREDENTIALS"
    assert expired.json() == revoked.json() == invalid.json()
    assert wrong_role.status_code == 403


async def test_login_failure_rate_limit_returns_retry_after() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        failures = [
            await client.post(
                "/api/auth/login",
                json={"employee_id": "CS1024", "password": "wrong"},
            )
            for _ in range(TEST_SETTINGS.agent_login_failure_limit)
        ]
        limited = await client.post(
            "/api/auth/login",
            json={"employee_id": "CS1024", "password": "wrong"},
        )

    assert {response.status_code for response in failures} == {401}
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


async def test_card_list_exposes_only_minimal_data_and_time_boundaries() -> None:
    active_id, _ = await _create_card(reference="KBSOS-" + "A" * 26)
    expired_id, _ = await _create_card(
        reference="KBSOS-" + "B" * 26,
        received_at=FIXED_NOW - timedelta(hours=2),
        expires_at=FIXED_NOW,
    )
    await _create_card(
        reference="KBSOS-" + "C" * 26,
        received_at=FIXED_NOW - timedelta(hours=72),
        expires_at=FIXED_NOW + timedelta(hours=1),
        purge_at=FIXED_NOW,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        response = await client.get("/api/agent/consultation-cards", headers=_auth(token))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = {item["card_id"]: item for item in response.json()["items"]}
    assert set(items) == {str(active_id), str(expired_id)}
    assert items[str(active_id)]["expired"] is False
    assert items[str(active_id)]["can_open"] is True
    assert items[str(expired_id)]["expired"] is True
    assert items[str(expired_id)]["can_open"] is False
    forbidden = {
        "session_digest",
        "masked_text",
        "symbol_name",
        "symbol_code",
        "quantity",
        "price_krw",
        "reference_number",
        "reference_digest",
        "object_key",
        "token",
    }
    assert all(forbidden.isdisjoint(item) for item in items.values())


async def test_lookup_supports_reference_and_card_id_without_attachment_url() -> None:
    card_id, _ = await _create_card(with_attachment=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        by_reference = await client.post(
            "/api/consultation-cards/lookup",
            headers=_auth(token),
            json={"reference_number": REFERENCE},
        )
        by_card_id = await client.post(
            "/api/consultation-cards/lookup",
            headers=_auth(token),
            json={"card_id": str(card_id)},
        )

    assert by_reference.status_code == by_card_id.status_code == 200
    assert by_reference.json() == by_card_id.json()
    body = by_reference.json()
    assert body["card_id"] == str(card_id)
    assert body["has_attachment"] is True
    assert body["related_signals"] == []
    assert {"reference_number", "reference_digest", "attachment_url", "object_key"}.isdisjoint(body)
    assert by_reference.headers["cache-control"] == "no-store"


async def test_missing_expired_and_deleted_cards_share_the_same_404() -> None:
    expired_id, _ = await _create_card(expires_at=FIXED_NOW)
    deleted_id, report_id = await _create_card(reference="KBSOS-" + "B" * 26)
    async with session_factory() as session, session.begin():
        await session.execute(delete(Report).where(Report.id == report_id))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        responses = [
            await client.post(
                "/api/consultation-cards/lookup",
                headers=_auth(token),
                json={"card_id": str(card_id)},
            )
            for card_id in (expired_id, deleted_id, uuid4())
        ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.text for response in responses}) == 1
    assert all(response.headers["cache-control"] == "no-store" for response in responses)


async def test_lookup_rate_limit_is_atomic_across_concurrent_requests() -> None:
    card_id, _ = await _create_card()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)

        async def lookup() -> int:
            response = await client.post(
                "/api/consultation-cards/lookup",
                headers=_auth(token),
                json={"card_id": str(card_id)},
            )
            if response.status_code == 429:
                assert int(response.headers["retry-after"]) >= 1
            return response.status_code

        statuses = await asyncio.gather(*(lookup() for _ in range(12)))

    assert statuses.count(200) == TEST_SETTINGS.agent_lookup_limit
    assert statuses.count(429) == 2


async def test_verification_stores_matched_needs_confirmation_and_important() -> None:
    card_id, _ = await _create_card()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        matched = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=_verification_payload(card_id),
        )
        needs_payload = _verification_payload(card_id)
        needs_payload["symbol_name"] = None
        needs = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=needs_payload,
        )
        important_payload = _verification_payload(card_id)
        important_payload["action"] = "BUY"
        important = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=important_payload,
        )
        history_payload = _verification_payload(card_id)
        history_payload["order_history_checked"] = False
        history = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=history_payload,
        )

    assert (
        matched.status_code
        == needs.status_code
        == important.status_code
        == history.status_code
        == 200
    )
    assert matched.json()["status"] == "MATCHED"
    assert needs.json()["status"] == "NEEDS_CONFIRMATION"
    assert important.json()["status"] == "IMPORTANT"
    assert important.json()["mismatch_fields"] == ["action"]
    assert history.json()["status"] == "NEEDS_CONFIRMATION"
    assert history.json()["mismatch_fields"] == []
    assert all(
        response.headers["cache-control"] == "no-store" for response in (matched, needs, important)
    )


async def test_agent_verification_enforces_active_symbol_master() -> None:
    card_id, _ = await _create_card()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        mismatch_payload = _verification_payload(card_id)
        mismatch_payload["symbol_name"] = "다른종목"
        mismatch = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=mismatch_payload,
        )
        unsupported_payload = _verification_payload(card_id)
        unsupported_payload["symbol_code"] = "999999"
        unsupported = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=unsupported_payload,
        )

        async with session_factory() as session, session.begin():
            await session.execute(
                update(SymbolMasterVersion)
                .where(SymbolMasterVersion.is_active.is_(True))
                .values(is_active=False)
            )
        unavailable = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=_verification_payload(card_id),
        )
        unknown_payload = _verification_payload(card_id)
        unknown_payload["symbol_name"] = None
        unknown_payload["symbol_code"] = None
        unknown = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=unknown_payload,
        )

    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "SYMBOL_MISMATCH"
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "UNSUPPORTED_SYMBOL"
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "SYMBOL_MASTER_UNAVAILABLE"
    assert unknown.status_code == 200
    assert unknown.json()["status"] == "NEEDS_CONFIRMATION"
    async with session_factory() as session:
        saved = await session.scalar(select(AgentVerification))
        assert saved is not None and saved.symbol_master_version_id is None


async def test_verification_idempotency_replay_conflict_and_concurrency() -> None:
    card_id, _ = await _create_card()
    request_id = uuid4()
    payload = _verification_payload(card_id, request_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)

        async def save(body: dict[str, object]) -> tuple[int, dict[str, object]]:
            response = await client.post(
                "/api/consultation-cards/verifications",
                headers=_auth(token),
                json=body,
            )
            return response.status_code, response.json()

        first, concurrent_replay = await asyncio.gather(save(payload), save(payload))
        replay = await save(payload)
        conflict_payload = {**payload, "quantity": 4}
        conflict = await save(conflict_payload)

    assert first[0] == concurrent_replay[0] == replay[0] == 200
    assert first[1]["verification_id"] == concurrent_replay[1]["verification_id"]
    assert first[1] == replay[1]
    assert conflict[0] == 409
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AgentVerification)) == 1


async def test_expired_card_rejects_verification_and_report_purge_cascades() -> None:
    card_id, report_id = await _create_card()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        saved = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=_verification_payload(card_id),
        )
        assert saved.status_code == 200

    async with session_factory() as session, session.begin():
        report = await session.get(Report, report_id)
        assert report is not None
        report.purge_at = FIXED_NOW
    async with session_factory() as session:
        await purge_expired_data(
            session,
            LocalAttachmentStore(Path(".test-artifacts") / "agent-purge"),
            now=FIXED_NOW,
            batch_size=10,
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AgentVerification)) == 0

    expired_card_id, _ = await _create_card(expires_at=FIXED_NOW)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        expired = await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=_verification_payload(expired_card_id),
        )
    assert expired.status_code == 404


async def test_audit_and_error_surfaces_do_not_expose_secrets_or_order_details() -> None:
    card_id, _ = await _create_card()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        await client.post(
            "/api/consultation-cards/lookup",
            headers=_auth(token),
            json={"reference_number": REFERENCE},
        )
        await client.post(
            "/api/consultation-cards/verifications",
            headers=_auth(token),
            json=_verification_payload(card_id),
        )
        failure = await client.post(
            "/api/auth/login", json={"employee_id": "CS1024", "password": "wrong"}
        )

    async with session_factory() as session:
        audits = list((await session.scalars(select(AuditLog))).all())
    serialized = " ".join(
        f"{audit.actor_id} {audit.actor_type} {audit.action} {audit.outcome} "
        f"{audit.resource_fingerprint}"
        for audit in audits
    )
    for secret in ("demo", "wrong", token, "CS1024", REFERENCE, "합성종목", "000001", "70000"):
        assert secret not in serialized
        assert secret not in failure.text


async def test_seed_is_idempotent_and_environment_password_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_AGENT_PASSWORD", "demo")
    arguments = Namespace(
        employee_id="CS1024",
        agent_label="갱신된 데모 상담원",
        role="AGENT",
        password="must-not-be-used",
    )

    await seed_agent(arguments)
    await seed_agent(arguments)

    async with session_factory() as session:
        accounts = list(
            (
                await session.scalars(
                    select(AgentAccount).where(AgentAccount.employee_id == "CS1024")
                )
            ).all()
        )
    assert len(accounts) == 1
    assert accounts[0].agent_label == "갱신된 데모 상담원"
    assert verify_password("demo", accounts[0].password_hash)
    assert not verify_password("must-not-be-used", accounts[0].password_hash)


async def test_purge_removes_expired_agent_tokens_and_rate_buckets() -> None:
    token = make_opaque_token()
    async with session_factory() as session, session.begin():
        account = await session.scalar(
            select(AgentAccount).where(AgentAccount.employee_id == "CS1024")
        )
        assert account is not None
        session.add_all(
            (
                AgentAccessToken(
                    agent_id=account.id,
                    token_digest=opaque_token_digest(
                        token,
                        TEST_SETTINGS.agent_token_hmac_key.get_secret_value().encode(),
                    ),
                    created_at=FIXED_NOW - timedelta(minutes=30),
                    expires_at=FIXED_NOW,
                ),
                RateLimitBucket(
                    scope="AGENT_CARD_LOOKUP",
                    principal_fingerprint=b"p" * 32,
                    client_fingerprint=b"i" * 32,
                    window_started_at=FIXED_NOW - timedelta(minutes=1),
                    request_count=1,
                    expires_at=FIXED_NOW,
                    updated_at=FIXED_NOW,
                ),
            )
        )

    async with session_factory() as session:
        result = await purge_expired_data(
            session,
            LocalAttachmentStore(Path(".test-artifacts") / "agent-purge-independent"),
            now=FIXED_NOW,
            batch_size=10,
        )
    assert result.agent_tokens_deleted == 1
    assert result.rate_limit_buckets_deleted == 1
