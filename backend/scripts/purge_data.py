import argparse
import asyncio
import json

from app.attachments import get_attachment_store
from app.db import engine, session_factory
from app.services.lifecycle import preview_purge, purge_expired_data, utc_now


def _positive_batch_size(value: str) -> int:
    size = int(value)
    if not 1 <= size <= 1000:
        raise argparse.ArgumentTypeError("batch size must be between 1 and 1000")
    return size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purge expired backend data safely")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="physically delete eligible data; omitted means dry-run",
    )
    parser.add_argument("--batch-size", type=_positive_batch_size, default=100)
    return parser.parse_args()


async def run(*, execute: bool, batch_size: int) -> None:
    now = utc_now()
    try:
        async with session_factory() as session:
            if not execute:
                preview = await preview_purge(session, now=now)
                independent_records = (
                    preview.idempotency_records
                    + preview.audit_logs
                    + preview.completed_deletion_jobs
                    + preview.expired_agent_tokens
                    + preview.expired_rate_limit_buckets
                    + preview.signal_clusters
                    + preview.signal_audit_events
                )
                object_candidates = preview.attachment_objects + preview.retry_ready_objects
                print(
                    json.dumps(
                        {
                            "event": "retention_worker_batch",
                            "mode": "dry-run",
                            "reports": preview.reports,
                            "signal_clusters": preview.signal_clusters,
                            "signal_audit_events": preview.signal_audit_events,
                            "independent_records": independent_records,
                            "objects": object_candidates,
                        },
                        separators=(",", ":"),
                    )
                )
                return

            result = await purge_expired_data(
                session,
                get_attachment_store(),
                now=now,
                batch_size=batch_size,
            )
            independent_deleted = (
                result.idempotency_deleted
                + result.audit_logs_deleted
                + result.deletion_jobs_deleted
                + result.agent_tokens_deleted
                + result.rate_limit_buckets_deleted
                + result.signal_clusters_deleted
                + result.signal_audit_events_deleted
            )
            print(
                json.dumps(
                    {
                        "event": "retention_worker_batch",
                        "mode": "execute",
                        "reports_deleted": result.reports_deleted,
                        "independent_records_deleted": independent_deleted,
                        "object_deletions_succeeded": result.object_deletions_succeeded,
                        "object_deletions_failed": result.object_deletions_failed,
                        "object_deletions_skipped": result.object_deletions_skipped,
                        "retry_waiting": result.retry_waiting,
                    },
                    separators=(",", ":"),
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(execute=arguments.execute, batch_size=arguments.batch_size))
