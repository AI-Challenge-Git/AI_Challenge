"""Deterministic customer-report to incident-signal relevance policy."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.clustering import EXCLUDED_ISSUE_TYPES, SIMILARITY_THRESHOLD, cosine_similarity
from app.codes import IssueType

SIGNAL_RELEVANCE_POLICY_VERSION = "signal-relevance.v1"
OCCURRED_AT_CONFIRMATION_QUESTION = (
    "고객이 문제를 겪은 시각이 현재 장애 신호의 발생 구간과 일치하는지 확인해 주세요."
)


class SignalRelevanceStatus(StrEnum):
    RELATED = "RELATED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    NOT_RELATED = "NOT_RELATED"


class SignalRelevanceReason(StrEnum):
    INELIGIBLE_ISSUE_TYPE = "INELIGIBLE_ISSUE_TYPE"
    ISSUE_TYPE_MISMATCH = "ISSUE_TYPE_MISMATCH"
    SIMILARITY_BELOW_THRESHOLD = "SIMILARITY_BELOW_THRESHOLD"
    OCCURRED_AT_MISSING = "OCCURRED_AT_MISSING"
    OUTSIDE_SIGNAL_WINDOW = "OUTSIDE_SIGNAL_WINDOW"
    ALL_GATES_PASSED = "ALL_GATES_PASSED"


@dataclass(frozen=True, slots=True)
class CustomerSignalCandidate:
    report_id: str
    issue_type: IssueType
    symptom_embedding: list[float]
    reported_occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class IncidentSignal:
    signal_id: str
    issue_type: IssueType
    representative_embedding: list[float]
    started_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SignalRelevanceResult:
    policy_version: str
    report_id: str
    signal_id: str
    status: SignalRelevanceStatus
    similarity: float | None
    threshold: float
    reasons: tuple[SignalRelevanceReason, ...]
    confirmation_questions: tuple[str, ...]


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")


def _result(
    customer: CustomerSignalCandidate,
    signal: IncidentSignal,
    status: SignalRelevanceStatus,
    reason: SignalRelevanceReason,
    *,
    similarity: float | None,
    threshold: float,
    confirmation_questions: tuple[str, ...] = (),
) -> SignalRelevanceResult:
    return SignalRelevanceResult(
        policy_version=SIGNAL_RELEVANCE_POLICY_VERSION,
        report_id=customer.report_id,
        signal_id=signal.signal_id,
        status=status,
        similarity=similarity,
        threshold=threshold,
        reasons=(reason,),
        confirmation_questions=confirmation_questions,
    )


def evaluate_signal_relevance(
    customer: CustomerSignalCandidate,
    signal: IncidentSignal,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> SignalRelevanceResult:
    """Evaluate relevance without using customer order details or other PII.

    threshold는 기본값으로 AI가 평가한 SIMILARITY_THRESHOLD(clustering.py)를 쓰지만,
    호출자가 활성 ClusteringPolicy.similarity_threshold(DB)를 명시적으로 넘기면
    그 값을 우선 사용한다. 전역 상수와 운영 DB 정책이 서로 다른 값을 갖는 문제를
    막기 위한 파라미터화다.
    """
    _require_aware_datetime(signal.started_at, "signal.started_at")
    if signal.ended_at is not None:
        _require_aware_datetime(signal.ended_at, "signal.ended_at")
        if signal.ended_at < signal.started_at:
            raise ValueError("signal.ended_at cannot be earlier than signal.started_at")
    if customer.reported_occurred_at is not None:
        _require_aware_datetime(
            customer.reported_occurred_at,
            "customer.reported_occurred_at",
        )

    if (
        customer.issue_type.value in EXCLUDED_ISSUE_TYPES
        or signal.issue_type.value in EXCLUDED_ISSUE_TYPES
    ):
        return _result(
            customer,
            signal,
            SignalRelevanceStatus.NOT_RELATED,
            SignalRelevanceReason.INELIGIBLE_ISSUE_TYPE,
            similarity=None,
            threshold=threshold,
        )

    if customer.issue_type is not signal.issue_type:
        return _result(
            customer,
            signal,
            SignalRelevanceStatus.NOT_RELATED,
            SignalRelevanceReason.ISSUE_TYPE_MISMATCH,
            similarity=None,
            threshold=threshold,
        )

    similarity = cosine_similarity(
        customer.symptom_embedding,
        signal.representative_embedding,
    )
    if similarity < threshold:
        return _result(
            customer,
            signal,
            SignalRelevanceStatus.NOT_RELATED,
            SignalRelevanceReason.SIMILARITY_BELOW_THRESHOLD,
            similarity=similarity,
            threshold=threshold,
        )

    if customer.reported_occurred_at is None:
        return _result(
            customer,
            signal,
            SignalRelevanceStatus.NEEDS_CONFIRMATION,
            SignalRelevanceReason.OCCURRED_AT_MISSING,
            similarity=similarity,
            threshold=threshold,
            confirmation_questions=(OCCURRED_AT_CONFIRMATION_QUESTION,),
        )

    occurred_at = customer.reported_occurred_at
    if occurred_at < signal.started_at or (
        signal.ended_at is not None and occurred_at > signal.ended_at
    ):
        return _result(
            customer,
            signal,
            SignalRelevanceStatus.NOT_RELATED,
            SignalRelevanceReason.OUTSIDE_SIGNAL_WINDOW,
            similarity=similarity,
            threshold=threshold,
        )

    return _result(
        customer,
        signal,
        SignalRelevanceStatus.RELATED,
        SignalRelevanceReason.ALL_GATES_PASSED,
        similarity=similarity,
        threshold=threshold,
    )
