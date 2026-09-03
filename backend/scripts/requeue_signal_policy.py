import argparse
import asyncio
import json

from app.db import engine, session_factory
from app.services.lifecycle import utc_now
from app.services.signals import requeue_signal_processing_for_policy


def _batch_size(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1000:
        raise argparse.ArgumentTypeError("batch size must be between 1 and 1000")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Requeue retained confirmed reports for an active signal policy"
    )
    parser.add_argument("--policy-version", required=True)
    parser.add_argument("--batch-size", type=_batch_size, default=100)
    return parser.parse_args()


async def run(*, policy_version: str, batch_size: int) -> int:
    try:
        async with session_factory() as session:
            created, reset = await requeue_signal_processing_for_policy(
                session,
                policy_version=policy_version,
                now=utc_now(),
                limit=batch_size,
            )
        print(
            json.dumps(
                {
                    "event": "signal_policy_requeue",
                    "policy_version": policy_version,
                    "created": created,
                    "reset": reset,
                },
                separators=(",", ":"),
            )
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                policy_version=arguments.policy_version,
                batch_size=arguments.batch_size,
            )
        )
    )
