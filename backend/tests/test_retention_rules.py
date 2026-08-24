from datetime import UTC, datetime, timedelta

import pytest

from app.services.lifecycle import (
    CARD_ACCESS_TTL,
    RETENTION_PERIOD,
    card_is_accessible,
    retention_deadline,
)


def test_card_access_ttl_boundary_is_exclusive() -> None:
    issued_at = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    expires_at = issued_at + CARD_ACCESS_TTL

    assert card_is_accessible(expires_at, now=expires_at - timedelta(microseconds=1))
    assert not card_is_accessible(expires_at, now=expires_at)
    assert not card_is_accessible(expires_at, now=expires_at + timedelta(microseconds=1))


def test_report_retention_boundary_is_received_at_plus_72_hours() -> None:
    received_at = datetime(2026, 8, 21, 3, 30, tzinfo=UTC)

    assert retention_deadline(received_at) == received_at + RETENTION_PERIOD
    assert retention_deadline(received_at) == datetime(2026, 8, 24, 3, 30, tzinfo=UTC)


def test_lifecycle_time_helpers_reject_naive_time() -> None:
    naive = datetime(2026, 8, 24, 3, 30)

    with pytest.raises(ValueError, match="timezone-aware"):
        retention_deadline(naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        card_is_accessible(datetime(2026, 8, 24, 5, 30, tzinfo=UTC), now=naive)
