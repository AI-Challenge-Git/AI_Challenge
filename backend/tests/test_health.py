from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client


async def test_live(client: AsyncClient) -> None:
    database_was_called = False

    async def forbidden_session() -> AsyncIterator[AsyncSession]:
        nonlocal database_was_called
        database_was_called = True
        yield AsyncMock(spec=AsyncSession)

    app.dependency_overrides[get_session] = forbidden_session
    response = await client.get("/health/live")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    assert database_was_called is False


async def test_ready_when_runtime_dependencies_are_available(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    readiness = AsyncMock(return_value=())
    monkeypatch.setattr("app.api.health.collect_service_readiness_failures", readiness)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = await client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    readiness.assert_awaited_once()


async def test_ready_when_required_runtime_data_is_missing(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(
        "app.api.health.collect_service_readiness_failures",
        AsyncMock(return_value=("ACTIVE_SIGNAL_POLICY_MISSING",)),
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = await client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "required runtime data unavailable"}


@pytest.mark.parametrize(
    "database_error",
    [
        SQLAlchemyError("driver failure"),
        OSError("network failure"),
    ],
)
async def test_ready_when_database_is_unavailable(
    client: AsyncClient,
    database_error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    secret = "do-not-leak-this-password"
    database_error.args = (f"postgresql+asyncpg://user:{secret}@db:5432/mts_sos",)
    monkeypatch.setattr(
        "app.api.health.collect_service_readiness_failures",
        AsyncMock(side_effect=database_error),
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = await client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert secret not in response.text
