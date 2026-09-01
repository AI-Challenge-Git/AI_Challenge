import argparse
import asyncio

from sqlalchemy import select

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
    return parser.parse_args()


async def run(*, max_jobs: int) -> None:
    completed = 0
    failed = 0
    try:
        async with session_factory() as session:
            policy = await session.scalar(
                select(ClusteringPolicy).where(ClusteringPolicy.is_active.is_(True))
            )
        if policy is None:
            print("processed=0 completed=0 failed=0")
            return
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
        for _ in range(max_jobs):
            async with session_factory() as session:
                result = await process_next_signal_job(session, provider, now=utc_now())
            if result is None:
                break
            if result.safe_error_code is None:
                completed += 1
            else:
                failed += 1
        print(f"processed={completed + failed} completed={completed} failed={failed}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(max_jobs=arguments.max_jobs))
