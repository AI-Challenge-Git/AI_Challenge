import asyncio
import json

from app.db import engine, session_factory
from app.services.lifecycle import utc_now
from app.services.operations import collect_operational_metrics, operational_alerts


async def run() -> int:
    try:
        async with session_factory() as session:
            metrics = await collect_operational_metrics(session, now=utc_now())
        alerts = operational_alerts(metrics)
        print(
            json.dumps(
                {
                    "event": "operational_health",
                    **metrics.model_dump(mode="json"),
                    "alerts": alerts,
                },
                separators=(",", ":"),
            )
        )
        return 1 if alerts else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
