from datetime import date

import pytest

from app.models import PolicySnapshot
from app.services.policies import (
    InvalidPolicySnapshotError,
    consultation_safety_notice,
    policy_content_sha256,
)


def _policy(content: dict[str, object]) -> PolicySnapshot:
    return PolicySnapshot(
        version="policy.test.v1",
        source_url="https://example.invalid/policy",
        source_checked_on=date(2026, 9, 2),
        content=content,
        content_sha256=policy_content_sha256(content),
    )


def test_consultation_notice_comes_from_verified_policy_content() -> None:
    policy = _policy({"title": "합성 정책", "notice": "합성 안전 안내"})

    assert consultation_safety_notice(policy) == "합성 안전 안내"


@pytest.mark.parametrize("invalid", ["hash", "url", "notice"])
def test_invalid_policy_snapshot_is_rejected(invalid: str) -> None:
    policy = _policy({"title": "합성 정책", "notice": "합성 안전 안내"})
    if invalid == "hash":
        policy.content_sha256 = "0" * 64
    elif invalid == "url":
        policy.source_url = "http://example.invalid/policy"
    else:
        policy.content = {"title": "합성 정책"}
        policy.content_sha256 = policy_content_sha256(policy.content)

    with pytest.raises(InvalidPolicySnapshotError):
        consultation_safety_notice(policy)
