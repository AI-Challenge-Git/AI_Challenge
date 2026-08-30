import argparse
import asyncio

from app.db import engine, session_factory
from app.services.lifecycle import utc_now
from app.services.signal_embeddings import OpenAiSignalEmbeddingAdapter
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
    provider = OpenAiSignalEmbeddingAdapter()
    completed = 0
    failed = 0
    try:
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
