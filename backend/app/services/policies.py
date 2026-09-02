import hashlib
import json
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.models import PolicySnapshot


class InvalidPolicySnapshotError(ValueError):
    pass


def policy_content_sha256(content: Mapping[str, object]) -> str:
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def consultation_safety_notice(policy: PolicySnapshot) -> str:
    parsed_url = urlsplit(policy.source_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise InvalidPolicySnapshotError("policy source URL is invalid")
    if policy_content_sha256(policy.content) != policy.content_sha256:
        raise InvalidPolicySnapshotError("policy content hash does not match")
    title = policy.content.get("title")
    notice = policy.content.get("notice")
    if not isinstance(title, str) or not title.strip():
        raise InvalidPolicySnapshotError("policy title is missing")
    if not isinstance(notice, str) or not notice.strip():
        raise InvalidPolicySnapshotError("policy consultation notice is missing")
    return notice.strip()
