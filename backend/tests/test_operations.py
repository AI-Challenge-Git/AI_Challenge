from datetime import UTC, datetime

from app.schemas import OperationalMetricsResponse
from app.services.operations import operational_alerts


def _metrics(**overrides: int) -> OperationalMetricsResponse:
    values = {
        "signal_jobs_ready": 0,
        "signal_jobs_repeated_failures": 0,
        "signal_jobs_dead_letter": 0,
        "provider_failures_last_15m": 0,
        "object_deletion_jobs_ready": 0,
        "object_deletion_jobs_retrying": 0,
    }
    values.update(overrides)
    return OperationalMetricsResponse(
        observed_at=datetime(2026, 9, 2, tzinfo=UTC),
        **values,
    )


def test_operational_alerts_are_empty_for_backlog_without_failures() -> None:
    assert operational_alerts(_metrics(signal_jobs_ready=5, object_deletion_jobs_ready=2)) == ()


def test_operational_alerts_cover_retries_dead_letters_provider_and_storage() -> None:
    assert operational_alerts(
        _metrics(
            signal_jobs_repeated_failures=1,
            signal_jobs_dead_letter=1,
            provider_failures_last_15m=1,
            object_deletion_jobs_retrying=1,
        )
    ) == (
        "SIGNAL_JOB_REPEATED_FAILURE",
        "SIGNAL_JOB_DEAD_LETTER",
        "AI_PROVIDER_FAILURE",
        "OBJECT_DELETION_RETRY",
    )
