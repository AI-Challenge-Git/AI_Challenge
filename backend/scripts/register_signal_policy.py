import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.codes import ClusteringPolicyStatus
from app.db import engine, session_factory
from app.models import ClusteringPolicy


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _threshold(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed <= 1:
        raise argparse.ArgumentTypeError("threshold must be in (0, 1]")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register an immutable EXPERIMENTAL signal policy")
    parser.add_argument("--policy-version", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--dimension", required=True, type=_positive_int)
    parser.add_argument("--normalization", required=True, choices=("L2", "NONE"))
    parser.add_argument("--input-format", required=True)
    parser.add_argument("--taxonomy-version", required=True)
    parser.add_argument("--window-seconds", type=_positive_int, default=600)
    parser.add_argument("--min-unique-sessions", type=_positive_int, default=5)
    parser.add_argument("--review-priority-threshold", type=_positive_int, default=10)
    parser.add_argument("--similarity-threshold", type=_threshold, default=0.80)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="make this the one active policy after metadata has been reviewed",
    )
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    if arguments.review_priority_threshold < arguments.min_unique_sessions:
        raise ValueError("review threshold cannot be below minimum unique sessions")
    try:
        async with session_factory() as session, session.begin():
            await session.execute(select(ClusteringPolicy.id).with_for_update())
            existing = await session.scalar(
                select(ClusteringPolicy).where(
                    ClusteringPolicy.policy_version == arguments.policy_version
                )
            )
            if existing is not None:
                raise ValueError("policy_version already exists; policies are immutable")
            if arguments.activate:
                current = await session.scalar(
                    select(ClusteringPolicy)
                    .where(ClusteringPolicy.is_active.is_(True))
                    .with_for_update()
                )
                if current is not None:
                    current.is_active = False
            policy = ClusteringPolicy(
                policy_version=arguments.policy_version,
                status=ClusteringPolicyStatus.EXPERIMENTAL.value,
                is_active=arguments.activate,
                window_seconds=arguments.window_seconds,
                min_unique_sessions=arguments.min_unique_sessions,
                review_priority_threshold=arguments.review_priority_threshold,
                similarity_threshold=arguments.similarity_threshold,
                structured_rules_version="hard-gate.v1",
                model_id=arguments.model_id,
                model_revision=arguments.model_revision,
                embedding_dimension=arguments.dimension,
                normalization=arguments.normalization,
                input_format=arguments.input_format,
                distance_metric="COSINE",
                taxonomy_version=arguments.taxonomy_version,
                created_at=datetime.now(UTC),
            )
            session.add(policy)
            await session.flush()
            print(
                f"policy_registered=true active={str(policy.is_active).lower()} status=EXPERIMENTAL"
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
