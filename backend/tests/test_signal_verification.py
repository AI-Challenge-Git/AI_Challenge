from dataclasses import replace

import pytest

from app.codes import VerificationStatus
from app.signal_relevance import (
    SIGNAL_RELEVANCE_POLICY_VERSION,
    SignalRelevanceReason,
    SignalRelevanceResult,
    SignalRelevanceStatus,
)
from app.signal_verification import (
    SIGNAL_VERIFICATION_POLICY_VERSION,
    AgentSignalDecision,
    SignalVerificationReason,
    verify_signal_relevance,
)


def _relevance(status: SignalRelevanceStatus) -> SignalRelevanceResult:
    return SignalRelevanceResult(
        policy_version=SIGNAL_RELEVANCE_POLICY_VERSION,
        report_id="report-1",
        signal_id="signal-1",
        status=status,
        similarity=0.91,
        threshold=0.79,
        reasons=(SignalRelevanceReason.ALL_GATES_PASSED,),
        confirmation_questions=(),
    )


@pytest.mark.parametrize(
    ("ai_status", "agent_decision", "final_related"),
    [
        (SignalRelevanceStatus.RELATED, AgentSignalDecision.RELATED, True),
        (SignalRelevanceStatus.NOT_RELATED, AgentSignalDecision.NOT_RELATED, False),
    ],
)
def test_ai_and_agent_agreement_is_matched(
    ai_status: SignalRelevanceStatus,
    agent_decision: AgentSignalDecision,
    final_related: bool,
) -> None:
    result = verify_signal_relevance(_relevance(ai_status), agent_decision)

    assert result.status is VerificationStatus.MATCHED
    assert result.final_related is final_related
    assert result.reason is SignalVerificationReason.AI_AND_AGENT_AGREE
    assert result.requires_reconfirmation is False


@pytest.mark.parametrize(
    ("ai_status", "agent_decision"),
    [
        (SignalRelevanceStatus.RELATED, AgentSignalDecision.NOT_RELATED),
        (SignalRelevanceStatus.NOT_RELATED, AgentSignalDecision.RELATED),
    ],
)
def test_ai_and_agent_disagreement_is_important(
    ai_status: SignalRelevanceStatus,
    agent_decision: AgentSignalDecision,
) -> None:
    result = verify_signal_relevance(_relevance(ai_status), agent_decision)

    assert result.status is VerificationStatus.IMPORTANT
    assert result.final_related is None
    assert result.reason is SignalVerificationReason.AI_AND_AGENT_DISAGREE
    assert result.requires_reconfirmation is True


@pytest.mark.parametrize(
    ("agent_decision", "final_related"),
    [
        (AgentSignalDecision.RELATED, True),
        (AgentSignalDecision.NOT_RELATED, False),
    ],
)
def test_agent_can_resolve_ai_uncertainty(
    agent_decision: AgentSignalDecision,
    final_related: bool,
) -> None:
    relevance = replace(
        _relevance(SignalRelevanceStatus.NEEDS_CONFIRMATION),
        reasons=(SignalRelevanceReason.OCCURRED_AT_MISSING,),
        confirmation_questions=("발생 시각을 확인해 주세요.",),
    )

    result = verify_signal_relevance(relevance, agent_decision)

    assert result.status is VerificationStatus.MATCHED
    assert result.final_related is final_related
    assert result.reason is SignalVerificationReason.AGENT_RESOLVED_AI_UNCERTAINTY
    assert result.requires_reconfirmation is False


@pytest.mark.parametrize("ai_status", list(SignalRelevanceStatus))
def test_unconfirmed_agent_decision_never_finalizes_relevance(
    ai_status: SignalRelevanceStatus,
) -> None:
    result = verify_signal_relevance(
        _relevance(ai_status),
        AgentSignalDecision.UNCONFIRMED,
    )

    assert result.status is VerificationStatus.NEEDS_CONFIRMATION
    assert result.final_related is None
    assert result.reason is SignalVerificationReason.AGENT_CONFIRMATION_MISSING
    assert result.requires_reconfirmation is True
    assert result.policy_version == SIGNAL_VERIFICATION_POLICY_VERSION
    assert result.report_id == "report-1"
    assert result.signal_id == "signal-1"
