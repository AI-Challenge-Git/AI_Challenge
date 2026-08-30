import json
import os
from datetime import date
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from app.db import engine, session_factory
from app.errors import ServiceError
from app.models import Symbol, SymbolMasterVersion
from app.services.symbols import (
    ListedSnapshot,
    SymbolImportError,
    fetch_listed_snapshot,
    import_symbol_master,
    parse_symbol_csv,
    validate_symbol,
    verify_listed_snapshot,
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


class _ApiResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode()

    def __enter__(self) -> "_ApiResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def _api_payload(*items: dict[str, str], total_count: int) -> dict[str, Any]:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
            "body": {
                "totalCount": total_count,
                "items": {"item": list(items)},
            },
        }
    }


def test_listed_info_fetch_paginates_and_csv_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = iter(
        (
            _ApiResponse(
                _api_payload(
                    {
                        "basDt": "20260829",
                        "srtnCd": "A005930",
                        "itmsNm": "삼성전자",
                        "mrktCtg": "KOSPI",
                    },
                    {
                        "basDt": "20260829",
                        "srtnCd": "A999999",
                        "itmsNm": "KONEX종목",
                        "mrktCtg": "KONEX",
                    },
                    total_count=3,
                )
            ),
            _ApiResponse(
                _api_payload(
                    {
                        "basDt": "20260829",
                        "srtnCd": "A0011A0",
                        "itmsNm": "액스비스",
                        "mrktCtg": "KOSDAQ",
                    },
                    total_count=3,
                )
            ),
        )
    )
    monkeypatch.setattr("app.services.symbols.urlopen", lambda *_args, **_kwargs: next(pages))
    snapshot = fetch_listed_snapshot(
        api_url="https://example.invalid/listed",
        api_key="synthetic-key",
        source_as_of=date(2026, 8, 29),
        page_size=2,
    )
    parsed = parse_symbol_csv(
        _csv(
            "005930,삼성전자,KOSPI,보통주",
            "0011A0,액스비스,KOSDAQ,보통주",
        )
    )

    assert [item.code for item in snapshot.items] == ["005930", "0011A0"]
    verify_listed_snapshot(parsed, snapshot)


def test_listed_info_mismatch_rejects_whole_sync() -> None:
    parsed = parse_symbol_csv(_csv("005930,삼성전자,KOSPI,보통주"))
    snapshot = ListedSnapshot(
        items=(),
        source_sha256="0" * 64,
        source_as_of=date(2026, 8, 29),
    )
    with pytest.raises(SymbolImportError, match="missing"):
        verify_listed_snapshot(parsed, snapshot)


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
