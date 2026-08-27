import argparse
import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.db import engine, session_factory
from app.services.symbols import (
    KRX_SOURCE_URL,
    SymbolImportError,
    import_symbol_master,
    load_symbol_csv,
)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a versioned KRX Symbol Master CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--as-of", type=_iso_date, required=True)
    parser.add_argument("--version")
    parser.add_argument("--source-url", default=KRX_SOURCE_URL)
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    version = arguments.version or f"krx-all-symbols-{arguments.as_of.isoformat()}"
    try:
        parsed = load_symbol_csv(arguments.csv_path)
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
            f"version={result.version} rows={result.row_count}"
        )
    except SymbolImportError as exc:
        raise SystemExit(f"symbol import rejected: {exc}") from None
    except SQLAlchemyError:
        raise SystemExit("symbol import failed: database unavailable") from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
