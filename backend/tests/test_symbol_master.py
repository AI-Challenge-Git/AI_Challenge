import os
from datetime import date

import pytest
from sqlalchemy import delete, func, select

from app.db import engine, session_factory
from app.errors import ServiceError
from app.models import Symbol, SymbolMasterVersion
from app.services.symbols import (
    SymbolImportError,
    import_symbol_master,
    parse_symbol_csv,
    validate_symbol,
)


def _csv(*rows: str, encoding: str = "utf-8-sig") -> bytes:
    text = "단축코드,한글 종목약명,시장구분,주식종류\r\n" + "\r\n".join(rows) + "\r\n"
    return text.encode(encoding)


def test_parser_accepts_utf8_sig_and_cp949_and_uses_only_explicit_source_fields() -> None:
    rows = (
        "005930,삼성전자,KOSPI,보통주",
        "035720,카카오,KOSDAQ,보통주",
        "123456,글로벌종목,KOSDAQ GLOBAL,보통주",
        "0011A0,액스비스,KOSDAQ,보통주",
        "999999,제외종목,KONEX,보통주",
        "111111,이름에ETF가있어도보통주,KOSPI,보통주",
        "222222,우선주,KOSPI,구형우선주",
    )

    utf8 = parse_symbol_csv(_csv(*rows))
    cp949 = parse_symbol_csv(_csv(*rows, encoding="cp949"))

    assert utf8.rows == cp949.rows
    assert utf8.source_encoding == "UTF-8-SIG"
    assert cp949.source_encoding == "CP949"
    assert [(row.code, row.market, row.source_market) for row in utf8.rows] == [
        ("005930", "KOSPI", "KOSPI"),
        ("035720", "KOSDAQ", "KOSDAQ"),
        ("123456", "KOSDAQ", "KOSDAQ GLOBAL"),
        ("0011A0", "KOSDAQ", "KOSDAQ"),
        ("111111", "KOSPI", "KOSPI"),
    ]


@pytest.mark.parametrize(
    "rows",
    [
        ("005930,삼성전자,KOSPI,보통주", "005930,중복,KOSDAQ,보통주"),
        ("5930,삼성전자,KOSPI,보통주",),
        ("0011a0,소문자코드,KOSDAQ,보통주",),
        ("005930,,KOSPI,보통주",),
        ("005930,삼성전자,KONEX,보통주",),
    ],
)
def test_parser_rejects_duplicate_invalid_or_empty_target_data(rows: tuple[str, ...]) -> None:
    with pytest.raises(SymbolImportError):
        parse_symbol_csv(_csv(*rows))


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires migrated PostgreSQL",
)
async def test_transactional_import_versions_and_validates_symbols() -> None:
    first_csv = parse_symbol_csv(
        _csv(
            "005930,삼성전자,KOSPI,보통주",
            "035720,카카오,KOSDAQ,보통주",
        )
    )
    second_csv = parse_symbol_csv(
        _csv(
            "000660,SK하이닉스,KOSPI,보통주",
            "0011A0,액스비스,KOSDAQ,보통주",
        )
    )
    async with session_factory() as session, session.begin():
        await session.execute(delete(SymbolMasterVersion))

    try:
        async with session_factory() as session:
            first = await import_symbol_master(
                session,
                first_csv,
                version="krx-test-1",
                source_as_of=date(2026, 8, 26),
            )
            replay = await import_symbol_master(
                session,
                first_csv,
                version="krx-test-1",
                source_as_of=date(2026, 8, 26),
            )
            second = await import_symbol_master(
                session,
                second_csv,
                version="krx-test-2",
                source_as_of=date(2026, 8, 27),
            )

        assert first.created is True
        assert replay == first.__class__(
            version_id=first.version_id,
            version=first.version,
            row_count=first.row_count,
            created=False,
        )
        assert second.created is True

        async with session_factory() as session:
            versions = list(
                (
                    await session.scalars(
                        select(SymbolMasterVersion).order_by(SymbolMasterVersion.version)
                    )
                ).all()
            )
            assert [version.is_active for version in versions] == [False, True]
            assert await session.scalar(select(func.count()).select_from(Symbol)) == 4
            assert (
                await validate_symbol(
                    session,
                    symbol_name="SK하이닉스",
                    symbol_code="000660",
                )
                == second.version_id
            )
            assert (
                await validate_symbol(
                    session,
                    symbol_name="액스비스",
                    symbol_code="0011A0",
                )
                == second.version_id
            )
            assert await validate_symbol(session, symbol_name=None, symbol_code=None) is None

            with pytest.raises(ServiceError) as unsupported:
                await validate_symbol(
                    session,
                    symbol_name="삼성전자",
                    symbol_code="005930",
                )
            with pytest.raises(ServiceError) as mismatch:
                await validate_symbol(
                    session,
                    symbol_name="다른종목",
                    symbol_code="000660",
                )
            assert (unsupported.value.status, unsupported.value.code) == (422, "UNSUPPORTED_SYMBOL")
            assert (mismatch.value.status, mismatch.value.code) == (422, "SYMBOL_MISMATCH")
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(delete(SymbolMasterVersion))
        await engine.dispose()
