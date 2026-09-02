import asyncio

from sqlalchemy import select

from app.db import session_factory
from app.models import ClusteringPolicy, SignalCluster, SignalProcessingJob


async def check() -> None:
    async with session_factory() as session:
        policies = (
            await session.execute(
                select(
                    ClusteringPolicy.policy_version,
                    ClusteringPolicy.status,
                    ClusteringPolicy.linkage_method,
                    ClusteringPolicy.representative_method,
                )
            )
        ).all()
        print("=== ClusteringPolicy (활성 정책, linkage/representative method) ===")
        if not policies:
            print("정책이 아예 없습니다 (등록도 안 됨).")
        for row in policies:
            print(row)

        jobs = (
            await session.execute(
                select(
                    SignalProcessingJob.status,
                    SignalProcessingJob.safe_error_code,
                )
            )
        ).all()
        print("\n=== SignalProcessingJob (제보 확정 시 생기는 작업) ===")
        if not jobs:
            print("job이 아예 없습니다 (제보 확정 자체가 안 됐을 수 있음).")
        for row in jobs:
            print(row)

        clusters = (
            await session.execute(
                select(
                    SignalCluster.status,
                    SignalCluster.raw_report_count,
                    SignalCluster.reporting_unique_sessions,
                    SignalCluster.reported_symptom_type,
                    SignalCluster.representative_symptom_id,
                )
            )
        ).all()
        print("\n=== SignalCluster (representative_symptom_id 포함) ===")
        if not clusters:
            print("signal_clusters 테이블이 비어있습니다.")
        for row in clusters:
            print(row)


asyncio.run(check())
