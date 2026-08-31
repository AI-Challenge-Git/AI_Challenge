from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.codes import BaselineStatus, IssueType, SignalStatus
from app.schemas import (
    OperatorMergeSignalsRequest,
    SignalDashboardItem,
    SignalEmbeddingResult,
)
from app.services.signals import is_signal_processing_eligible


def test_embedding_contract_validates_dimension_and_finite_values() -> None:
    valid = SignalEmbeddingResult(
        model_id="model",
        model_revision="revision",
        dimension=3,
        normalization="L2",
        input_format="query",
        distance_metric="COSINE",
        vector=[1.0, 0.0, 0.0],
    )
    assert valid.dimension == len(valid.vector)

    for vector in ([1.0, 0.0], [float("nan"), 0.0, 1.0], [0.0, 0.0, 0.0]):
        with pytest.raises(ValidationError):
            SignalEmbeddingResult(
                model_id="model",
                model_revision="revision",
                dimension=3,
                normalization="L2",
                input_format="query",
                distance_metric="COSINE",
                vector=vector,
            )


def test_dashboard_contract_rejects_internal_candidate_state() -> None:
    with pytest.raises(ValidationError):
        SignalDashboardItem.model_validate(
            {
                "signal_id": uuid4(),
                "status": SignalStatus.CANDIDATE,
                "channel": "MABLE",
                "feature_area": "DOMESTIC_STOCK_ORDER",
                "reported_symptom_type": IssueType.ORDER_SUBMISSION_FAILURE,
                "reporting_unique_sessions": 1,
                "raw_report_count": 1,
                "review_priority": False,
                "first_report_at": datetime.now(UTC),
                "last_report_at": datetime.now(UTC),
                "affected_features": ["DOMESTIC_STOCK_ORDER"],
                "policy_version": "experimental.v1",
                "policy_status": "EXPERIMENTAL",
                "baseline_status": BaselineStatus.INSUFFICIENT_HISTORY,
                "baseline_ratio": None,
                "official_incident": False,
                "official_notice_url": None,
            }
        )


@pytest.mark.parametrize("issue_type", ["UNKNOWN", "UNRELATED_OR_AMBIGUOUS"])
def test_signal_processing_excludes_non_actionable_issue_types(issue_type: str) -> None:
    assert not is_signal_processing_eligible(issue_type=issue_type, symptom="technical symptom")


def test_signal_processing_requires_a_confirmed_symptom() -> None:
    assert not is_signal_processing_eligible(
        issue_type="ORDER_SUBMISSION_FAILURE",
        symptom=None,
    )
    assert is_signal_processing_eligible(
        issue_type="ORDER_SUBMISSION_FAILURE",
        symptom="order button remains loading",
    )


def test_operator_merge_rejects_same_source_and_target() -> None:
    signal_id = uuid4()
    with pytest.raises(ValidationError):
        OperatorMergeSignalsRequest(
            source_signal_id=signal_id,
            target_signal_id=signal_id,
            reason="MANUAL_REVIEW",
            client_request_id=uuid4(),
        )
