"""Deterministic comparison of AI signal relevance and agent confirmation."""

from dataclasses import dataclass
from enum import StrEnum

from app.codes import VerificationStatus
from app.signal_relevance import SignalRelevanceResult, SignalRelevanceStatus

SIGNAL_VERIFICATION_POLICY_VERSION = "signal-verification.v1"


class AgentSignalDecision(StrEnum):
    RELATED = "RELATED"
    NOT_RELATED = "NOT_RELATED"
    UNCONFIRMED = "UNCONFIRMED"


class SignalVerificationReason(StrEnum):
    AGENT_CONFIRMATION_MISSING = "AGENT_CONFIRMATION_MISSING"
    AGENT_RESOLVED_AI_UNCERTAINTY = "AGENT_RESOLVED_AI_UNCERTAINTY"
    AI_AND_AGENT_AGREE = "AI_AND_AGENT_AGREE"
    AI_AND_AGENT_DISAGREE = "AI_AND_AGENT_DISAGREE"


@dataclass(frozen=True, slots=True)
class SignalVerificationResult:
    policy_version: str
    report_id: str
    signal_id: str
    ai_status: SignalRelevanceStatus
    agent_decision: AgentSignalDecision
    status: VerificationStatus
    final_related: bool | None
    reason: SignalVerificationReason
    requires_reconfirmation: bool


def verify_signal_relevance(
    relevance: SignalRelevanceResult,
    agent_decision: AgentSignalDecision,
) -> SignalVerificationResult:
    """Compare an agent decision with the AI result without using customer PII."""
    if agent_decision is AgentSignalDecision.UNCONFIRMED:
        return _result(
            relevance,
            agent_decision,
            status=VerificationStatus.NEEDS_CONFIRMATION,
            final_related=None,
            reason=SignalVerificationReason.AGENT_CONFIRMATION_MISSING,
            requires_reconfirmation=True,
        )

    agent_related = agent_decision is AgentSignalDecision.RELATED
    if relevance.status is SignalRelevanceStatus.NEEDS_CONFIRMATION:
        return _result(
            relevance,
            agent_decision,
            status=VerificationStatus.MATCHED,
            final_related=agent_related,
            reason=SignalVerificationReason.AGENT_RESOLVED_AI_UNCERTAINTY,
            requires_reconfirmation=False,
        )

    ai_related = relevance.status is SignalRelevanceStatus.RELATED
    if ai_related == agent_related:
        return _result(
            relevance,
            agent_decision,
            status=VerificationStatus.MATCHED,
            final_related=agent_related,
            reason=SignalVerificationReason.AI_AND_AGENT_AGREE,
            requires_reconfirmation=False,
        )

    return _result(
        relevance,
        agent_decision,
        status=VerificationStatus.IMPORTANT,
        final_related=None,
        reason=SignalVerificationReason.AI_AND_AGENT_DISAGREE,
        requires_reconfirmation=True,
    )


def _result(
    relevance: SignalRelevanceResult,
    agent_decision: AgentSignalDecision,
    *,
    status: VerificationStatus,
    final_related: bool | None,
    reason: SignalVerificationReason,
    requires_reconfirmation: bool,
) -> SignalVerificationResult:
    return SignalVerificationResult(
        policy_version=SIGNAL_VERIFICATION_POLICY_VERSION,
        report_id=relevance.report_id,
        signal_id=relevance.signal_id,
        ai_status=relevance.status,
        agent_decision=agent_decision,
        status=status,
        final_related=final_related,
        reason=reason,
        requires_reconfirmation=requires_reconfirmation,
    )
