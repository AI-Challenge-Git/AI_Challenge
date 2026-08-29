from datetime import UTC, datetime

import pytest

from app.clustering import SIMILARITY_THRESHOLD
from app.codes import IssueType
from app.signal_relevance import (
    OCCURRED_AT_CONFIRMATION_QUESTION,
    CustomerSignalCandidate,
    IncidentSignal,
    SignalRelevanceReason,
    SignalRelevanceStatus,
    evaluate_signal_relevance,
)


def _customer(
    *,
    issue_type: IssueType = IssueType.ORDER_SUBMISSION_FAILURE,
    embedding: list[float] | None = None,
    occurred_at: datetime | None = datetime(2026, 8, 29, 10, 30, tzinfo=UTC),
) -> CustomerSignalCandidate:
    return CustomerSignalCandidate(
        report_id="report-1",
        issue_type=issue_type,
        symptom_embedding=embedding or [1.0, 0.0],
        reported_occurred_at=occurred_at,
    )


def _signal(
    *,
    issue_type: IssueType = IssueType.ORDER_SUBMISSION_FAILURE,
    embedding: list[float] | None = None,
    started_at: datetime = datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
    ended_at: datetime | None = datetime(2026, 8, 29, 11, 0, tzinfo=UTC),
) -> IncidentSignal:
    return IncidentSignal(
        signal_id="signal-1",
        issue_type=issue_type,
        representative_embedding=embedding or [1.0, 0.0],
        started_at=started_at,
        ended_at=ended_at,
    )


def test_all_gates_pass_returns_related() -> None:
    result = evaluate_signal_relevance(_customer(), _signal())

    assert result.status is SignalRelevanceStatus.RELATED
    assert result.similarity == pytest.approx(1.0)
    assert result.threshold == SIMILARITY_THRESHOLD
    assert result.reasons == (SignalRelevanceReason.ALL_GATES_PASSED,)
    assert result.confirmation_questions == ()


def test_missing_customer_time_requires_fixed_confirmation_question() -> None:
    result = evaluate_signal_relevance(_customer(occurred_at=None), _signal())

    assert result.status is SignalRelevanceStatus.NEEDS_CONFIRMATION
    assert result.reasons == (SignalRelevanceReason.OCCURRED_AT_MISSING,)
    assert result.confirmation_questions == (OCCURRED_AT_CONFIRMATION_QUESTION,)


@pytest.mark.parametrize(
    "issue_type",
    [IssueType.UNKNOWN, IssueType.UNRELATED_OR_AMBIGUOUS],
)
def test_ineligible_issue_types_are_never_related(issue_type: IssueType) -> None:
    result = evaluate_signal_relevance(_customer(issue_type=issue_type), _signal())

    assert result.status is SignalRelevanceStatus.NOT_RELATED
    assert result.similarity is None
    assert result.reasons == (SignalRelevanceReason.INELIGIBLE_ISSUE_TYPE,)


def test_different_issue_types_are_not_compared_by_embedding() -> None:
    result = evaluate_signal_relevance(
        _customer(issue_type=IssueType.ORDER_SUBMISSION_FAILURE),
        _signal(issue_type=IssueType.ORDER_RESULT_UNCONFIRMED),
    )

    assert result.status is SignalRelevanceStatus.NOT_RELATED
    assert result.similarity is None
    assert result.reasons == (SignalRelevanceReason.ISSUE_TYPE_MISMATCH,)


def test_similarity_below_shared_clustering_threshold_is_not_related() -> None:
    result = evaluate_signal_relevance(
        _customer(embedding=[1.0, 0.0]),
        _signal(embedding=[0.0, 1.0]),
    )

    assert result.status is SignalRelevanceStatus.NOT_RELATED
    assert result.similarity == pytest.approx(0.0)
    assert result.reasons == (SignalRelevanceReason.SIMILARITY_BELOW_THRESHOLD,)


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(2026, 8, 29, 9, 59, tzinfo=UTC),
        datetime(2026, 8, 29, 11, 1, tzinfo=UTC),
    ],
)
def test_customer_time_outside_closed_signal_window_is_not_related(
    occurred_at: datetime,
) -> None:
    result = evaluate_signal_relevance(_customer(occurred_at=occurred_at), _signal())

    assert result.status is SignalRelevanceStatus.NOT_RELATED
    assert result.reasons == (SignalRelevanceReason.OUTSIDE_SIGNAL_WINDOW,)


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        datetime(2026, 8, 29, 11, 0, tzinfo=UTC),
    ],
)
def test_signal_window_boundaries_are_inclusive(occurred_at: datetime) -> None:
    result = evaluate_signal_relevance(_customer(occurred_at=occurred_at), _signal())

    assert result.status is SignalRelevanceStatus.RELATED


def test_open_signal_accepts_customer_time_after_start() -> None:
    result = evaluate_signal_relevance(
        _customer(occurred_at=datetime(2026, 8, 30, 10, 0, tzinfo=UTC)),
        _signal(ended_at=None),
    )

    assert result.status is SignalRelevanceStatus.RELATED


@pytest.mark.parametrize(
    ("customer", "signal"),
    [
        (_customer(occurred_at=datetime(2026, 8, 29, 10, 30)), _signal()),
        (_customer(), _signal(started_at=datetime(2026, 8, 29, 10, 0))),
        (_customer(), _signal(ended_at=datetime(2026, 8, 29, 11, 0))),
    ],
)
def test_naive_datetimes_are_rejected(
    customer: CustomerSignalCandidate,
    signal: IncidentSignal,
) -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        evaluate_signal_relevance(customer, signal)


def test_signal_end_cannot_precede_start() -> None:
    with pytest.raises(ValueError, match="cannot be earlier"):
        evaluate_signal_relevance(
            _customer(),
            _signal(
                started_at=datetime(2026, 8, 29, 11, 0, tzinfo=UTC),
                ended_at=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
            ),
        )
