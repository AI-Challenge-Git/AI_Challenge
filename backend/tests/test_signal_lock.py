from dataclasses import replace

import pytest

from app.codes import VerificationStatus
from app.signal_lock import (
    SIGNAL_LOCK_POLICY_VERSION,
    LockedSignalResult,
    SignalLockDecision,
    SignalLockReason,
    evaluate_signal_lock,
)
from app.signal_relevance import SignalRelevanceStatus
from app.signal_verification import (
    SIGNAL_VERIFICATION_POLICY_VERSION,
    AgentSignalDecision,
    SignalVerificationReason,
    SignalVerificationResult,
)


def _verification(*, final_related: bool = True) -> SignalVerificationResult:
    return SignalVerificationResult(
        policy_version=SIGNAL_VERIFICATION_POLICY_VERSION,
        report_id="report-1",
        signal_id="signal-1",
        ai_status=SignalRelevanceStatus.RELATED,
        agent_decision=AgentSignalDecision.RELATED,
        status=VerificationStatus.MATCHED,
        final_related=final_related,
        reason=SignalVerificationReason.AI_AND_AGENT_AGREE,
        requires_reconfirmation=False,
    )


@pytest.mark.parametrize("final_related", [True, False])
def test_matched_final_result_is_allowed(final_related: bool) -> None:
    result = evaluate_signal_lock(_verification(final_related=final_related))

    assert result.policy_version == SIGNAL_LOCK_POLICY_VERSION
    assert result.decision is SignalLockDecision.ALLOW
    assert result.reason is SignalLockReason.VERIFIED_RESULT
    assert result.proposed_result == LockedSignalResult(
        report_id="report-1",
        signal_id="signal-1",
        final_related=final_related,
        verification_policy_version=SIGNAL_VERIFICATION_POLICY_VERSION,
    )


@pytest.mark.parametrize(
    "status",
    [VerificationStatus.NEEDS_CONFIRMATION, VerificationStatus.IMPORTANT],
)
def test_unresolved_verification_is_blocked(status: VerificationStatus) -> None:
    verification = replace(
        _verification(),
        status=status,
        final_related=None,
        requires_reconfirmation=True,
    )

    result = evaluate_signal_lock(verification)

    assert result.decision is SignalLockDecision.BLOCK
    assert result.reason is SignalLockReason.VERIFICATION_NOT_MATCHED
    assert result.proposed_result is None


def test_reconfirmation_flag_blocks_even_matched_status() -> None:
    result = evaluate_signal_lock(replace(_verification(), requires_reconfirmation=True))

    assert result.decision is SignalLockDecision.BLOCK
    assert result.reason is SignalLockReason.RECONFIRMATION_REQUIRED


def test_missing_final_value_blocks_even_matched_status() -> None:
    result = evaluate_signal_lock(replace(_verification(), final_related=None))

    assert result.decision is SignalLockDecision.BLOCK
    assert result.reason is SignalLockReason.FINAL_RELEVANCE_MISSING


def test_same_existing_result_is_idempotent_replay() -> None:
    verification = _verification()
    existing = LockedSignalResult(
        report_id=verification.report_id,
        signal_id=verification.signal_id,
        final_related=True,
        verification_policy_version=verification.policy_version,
    )

    result = evaluate_signal_lock(verification, existing)

    assert result.decision is SignalLockDecision.IDEMPOTENT_REPLAY
    assert result.reason is SignalLockReason.SAME_RESULT_ALREADY_LOCKED
    assert result.proposed_result == existing


@pytest.mark.parametrize(
    "existing",
    [
        LockedSignalResult(
            report_id="report-1",
            signal_id="signal-1",
            final_related=False,
            verification_policy_version=SIGNAL_VERIFICATION_POLICY_VERSION,
        ),
        LockedSignalResult(
            report_id="other-report",
            signal_id="signal-1",
            final_related=True,
            verification_policy_version=SIGNAL_VERIFICATION_POLICY_VERSION,
        ),
        LockedSignalResult(
            report_id="report-1",
            signal_id="other-signal",
            final_related=True,
            verification_policy_version=SIGNAL_VERIFICATION_POLICY_VERSION,
        ),
        LockedSignalResult(
            report_id="report-1",
            signal_id="signal-1",
            final_related=True,
            verification_policy_version="older-policy",
        ),
    ],
)
def test_different_existing_lock_is_conflict(existing: LockedSignalResult) -> None:
    result = evaluate_signal_lock(_verification(), existing)

    assert result.decision is SignalLockDecision.CONFLICT
    assert result.reason is SignalLockReason.LOCKED_RESULT_DIFFERS
    assert result.proposed_result is not None
