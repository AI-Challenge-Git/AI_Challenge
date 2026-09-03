import argparse
import asyncio
import json

from sqlalchemy import select

from app.ai import OpenAIDualExtractorAdapter
from app.codes import SignalProcessingStatus
from app.config import get_settings
from app.db import engine, session_factory
from app.models import ClusteringPolicy
from app.services.lifecycle import utc_now
from app.services.signal_embeddings import (
    OpenAiSignalEmbeddingAdapter,
    embedding_contract_mismatches,
    load_signal_embedding_contract,
)
from app.services.signals import process_next_signal_job


def _job_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1000:
        raise argparse.ArgumentTypeError("max jobs must be between 1 and 1000")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process pending incident-signal jobs")
    parser.add_argument("--max-jobs", type=_job_limit, default=100)
    parser.add_argument(
        "--forever",
        action="store_true",
        help="keep polling for jobs instead of exiting after one bounded batch",
    )
    return parser.parse_args()


async def run(*, max_jobs: int, report_empty: bool = True) -> int:
    completed = 0
    failed = 0
    dead_lettered = 0
    try:
        async with session_factory() as session:
            policy = await session.scalar(
                select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
            )
        if policy is None:
            print(
                json.dumps(
                    {
                        "event": "signal_worker_batch",
                        "processed": 0,
                        "completed": 0,
                        "failed": 0,
                        "dead_lettered": 0,
                        "configuration_error": "ACTIVE_SIGNAL_POLICY_MISSING",
                    },
                    separators=(",", ":"),
                )
            )
            return 2
        if policy.taxonomy_version != OpenAIDualExtractorAdapter.taxonomy_version:
            raise RuntimeError("active signal policy does not match runtime extractor taxonomy")
        mismatches = embedding_contract_mismatches(
            load_signal_embedding_contract(),
            model_id=policy.model_id,
            model_revision=policy.model_revision,
            dimension=policy.embedding_dimension,
            normalization=policy.normalization,
            input_format=policy.input_format,
            distance_metric=policy.distance_metric,
        )
        if mismatches:
            raise RuntimeError(
                "active signal policy does not match runtime embedding contract: "
                + ", ".join(mismatches)
            )
        provider = OpenAiSignalEmbeddingAdapter()
        settings = get_settings()
        for _ in range(max_jobs):
            async with session_factory() as session:
                result = await process_next_signal_job(
                    session,
                    provider,
                    now=utc_now(),
                    max_attempts=settings.signal_worker_max_attempts,
                )
            if result is None:
                break
            if result.status is SignalProcessingStatus.COMPLETED:
                completed += 1
            elif result.status is SignalProcessingStatus.DEAD_LETTER:
                dead_lettered += 1
            else:
                failed += 1
        if completed or failed or dead_lettered or report_empty:
            print(
                json.dumps(
                    {
                        "event": "signal_worker_batch",
                        "processed": completed + failed + dead_lettered,
                        "completed": completed,
                        "failed": failed,
                        "dead_lettered": dead_lettered,
                    },
                    separators=(",", ":"),
                )
            )
        return 1 if failed or dead_lettered else 0
    finally:
        await engine.dispose()


async def run_forever(*, max_jobs: int) -> int:
    poll_seconds = get_settings().signal_worker_poll_seconds
    while True:
        exit_code = await run(max_jobs=max_jobs, report_empty=False)
        if exit_code == 2:
            return exit_code
        await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    arguments = parse_args()
    command = run_forever if arguments.forever else run
    raise SystemExit(asyncio.run(command(max_jobs=arguments.max_jobs)))
