import argparse
import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.symbols import (
    SymbolImportError,
    fetch_latest_listed_snapshot,
    fetch_listed_snapshot,
    reconcile_symbol_master,
)

KOREA_TIME_ZONE = ZoneInfo("Asia/Seoul")
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class KrxWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr
    krx_listed_info_api_url: str = (
        "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
    )
    krx_listed_info_api_key: SecretStr


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _lookback_days(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 31:
        raise argparse.ArgumentTypeError("lookback days must be between 1 and 31")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile the active common-stock master with the latest listed-info API"
    )
    parser.add_argument(
        "--as-of",
        type=_iso_date,
        help="request one exact basis date instead of discovering the latest available date",
    )
    parser.add_argument("--lookback-days", type=_lookback_days, default=14)
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    settings = KrxWorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    api_key = settings.krx_listed_info_api_key.get_secret_value()
    try:
        if arguments.as_of is None:
            snapshot = await asyncio.to_thread(
                fetch_latest_listed_snapshot,
                api_url=settings.krx_listed_info_api_url,
                api_key=api_key,
                today=datetime.now(KOREA_TIME_ZONE).date(),
                lookback_days=arguments.lookback_days,
            )
        else:
            snapshot = await asyncio.to_thread(
                fetch_listed_snapshot,
                api_url=settings.krx_listed_info_api_url,
                api_key=api_key,
                source_as_of=arguments.as_of,
            )
        async with session_factory() as session:
            result = await reconcile_symbol_master(
                session,
                snapshot,
                source_url=settings.krx_listed_info_api_url,
            )
        print(
            json.dumps(
                {
                    "event": "krx_symbol_reconciliation",
                    "status": "created" if result.created else "unchanged",
                    "version": result.version,
                    "source_as_of": result.source_as_of.isoformat(),
                    "rows": result.row_count,
                    "pending_missing": result.pending_missing_count,
                    "removed": result.removed_count,
                    "unknown_api_items": result.unknown_api_count,
                    "name_changes": result.name_change_count,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    except SymbolImportError as exc:
        raise SystemExit(f"symbol reconciliation rejected: {exc}") from None
    except SQLAlchemyError:
        raise SystemExit("symbol reconciliation failed: database unavailable") from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
