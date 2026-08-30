import argparse
import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import engine, session_factory
from app.services.symbols import (
    KRX_SOURCE_URL,
    SymbolImportError,
    fetch_listed_snapshot,
    import_symbol_master,
    load_symbol_csv,
    verify_listed_snapshot,
)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the KRX common-stock CSV against the daily listed-info API and import it"
        )
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--as-of", type=_iso_date, required=True)
    parser.add_argument("--version")
    parser.add_argument("--source-url", default=KRX_SOURCE_URL)
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    settings = get_settings()
    api_key = (
        settings.krx_listed_info_api_key.get_secret_value()
        if settings.krx_listed_info_api_key is not None
        else ""
    )
    version = arguments.version or f"krx-all-symbols-{arguments.as_of.isoformat()}"
    try:
        parsed = load_symbol_csv(arguments.csv_path)
        snapshot = await asyncio.to_thread(
            fetch_listed_snapshot,
            api_url=settings.krx_listed_info_api_url,
            api_key=api_key,
            source_as_of=arguments.as_of,
        )
        verify_listed_snapshot(parsed, snapshot)
        async with session_factory() as session:
            result = await import_symbol_master(
                session,
                parsed,
                version=version,
                source_as_of=arguments.as_of,
                source_url=arguments.source_url,
            )
        print(
            f"symbol_master={'created' if result.created else 'unchanged'} "
            f"version={result.version} rows={result.row_count} api_verified=true"
        )
    except SymbolImportError as exc:
        raise SystemExit(f"symbol sync rejected: {exc}") from None
    except SQLAlchemyError:
        raise SystemExit("symbol sync failed: database unavailable") from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
