"""Pure policy for deciding whether a verified signal result may be locked."""

from dataclasses import dataclass
from enum import StrEnum

from app.codes import VerificationStatus
from app.signal_verification import SignalVerificationResult

SIGNAL_LOCK_POLICY_VERSION = "signal-lock.v1"


class SignalLockDecision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    CONFLICT = "CONFLICT"


class SignalLockReason(StrEnum):
    VERIFIED_RESULT = "VERIFIED_RESULT"
    VERIFICATION_NOT_MATCHED = "VERIFICATION_NOT_MATCHED"
    RECONFIRMATION_REQUIRED = "RECONFIRMATION_REQUIRED"
    FINAL_RELEVANCE_MISSING = "FINAL_RELEVANCE_MISSING"
    SAME_RESULT_ALREADY_LOCKED = "SAME_RESULT_ALREADY_LOCKED"
    LOCKED_RESULT_DIFFERS = "LOCKED_RESULT_DIFFERS"


@dataclass(frozen=True, slots=True)
class LockedSignalResult:
    report_id: str
    signal_id: str
    final_related: bool
    verification_policy_version: str


@dataclass(frozen=True, slots=True)
class SignalLockEvaluation:
    policy_version: str
    decision: SignalLockDecision
    reason: SignalLockReason
    proposed_result: LockedSignalResult | None


def evaluate_signal_lock(
    verification: SignalVerificationResult,
    existing: LockedSignalResult | None = None,
) -> SignalLockEvaluation:
    """Evaluate lock eligibility without storing data or acquiring a DB lock."""
    if verification.status is not VerificationStatus.MATCHED:
        return _evaluation(
            SignalLockDecision.BLOCK,
            SignalLockReason.VERIFICATION_NOT_MATCHED,
        )
    if verification.requires_reconfirmation:
        return _evaluation(
            SignalLockDecision.BLOCK,
            SignalLockReason.RECONFIRMATION_REQUIRED,
        )
    if verification.final_related is None:
        return _evaluation(
            SignalLockDecision.BLOCK,
            SignalLockReason.FINAL_RELEVANCE_MISSING,
        )

    proposed = LockedSignalResult(
        report_id=verification.report_id,
        signal_id=verification.signal_id,
        final_related=verification.final_related,
        verification_policy_version=verification.policy_version,
    )
    if existing is None:
        return _evaluation(
            SignalLockDecision.ALLOW,
            SignalLockReason.VERIFIED_RESULT,
            proposed,
        )
    if existing == proposed:
        return _evaluation(
            SignalLockDecision.IDEMPOTENT_REPLAY,
            SignalLockReason.SAME_RESULT_ALREADY_LOCKED,
            proposed,
        )
    return _evaluation(
        SignalLockDecision.CONFLICT,
        SignalLockReason.LOCKED_RESULT_DIFFERS,
        proposed,
    )


def _evaluation(
    decision: SignalLockDecision,
    reason: SignalLockReason,
    proposed_result: LockedSignalResult | None = None,
) -> SignalLockEvaluation:
    return SignalLockEvaluation(
        policy_version=SIGNAL_LOCK_POLICY_VERSION,
        decision=decision,
        reason=reason,
        proposed_result=proposed_result,
    )
