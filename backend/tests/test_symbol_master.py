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
    ListedItem,
    ListedSnapshot,
    ListedSnapshotNotFound,
    SymbolImportError,
    fetch_latest_listed_snapshot,
    fetch_listed_snapshot,
    import_symbol_master,
    parse_symbol_csv,
    reconcile_symbol_master,
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

    def read(self, _size: int = -1) -> bytes:
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
    requested_urls: list[str] = []
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

    def fake_urlopen(request: Any, **_kwargs: Any) -> _ApiResponse:
        requested_urls.append(request.full_url)
        return next(pages)

    monkeypatch.setattr("app.services.symbols.urlopen", fake_urlopen)
    snapshot = fetch_listed_snapshot(
        api_url="https://example.invalid/listed",
        api_key="synthetic%2Bkey%3D",
        source_as_of=date(2026, 8, 29),
        page_size=2,
    )
    parsed = parse_symbol_csv(
        _csv(
            "005930,삼성전자,KOSPI,보통주",
            "0011A0,액스비스,KOSDAQ,보통주",
        )
    )

    assert [item.code for item in snapshot.items] == ["0011A0", "005930"]
    assert all("serviceKey=synthetic%2Bkey%3D" in url for url in requested_urls)
    assert all("%252B" not in url for url in requested_urls)
    verify_listed_snapshot(parsed, snapshot)


def test_latest_listed_snapshot_skips_dates_without_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted: list[date] = []
    expected = ListedSnapshot(
        items=(
            ListedItem(
                code="005930",
                name_ko="삼성전자",
                market="KOSPI",
                source_as_of=date(2026, 9, 4),
            ),
        ),
        source_sha256="0" * 64,
        source_as_of=date(2026, 9, 4),
    )

    def fake_fetch(**kwargs: Any) -> ListedSnapshot:
        source_as_of = kwargs["source_as_of"]
        attempted.append(source_as_of)
        if source_as_of != expected.source_as_of:
            raise ListedSnapshotNotFound("not published")
        return expected

    monkeypatch.setattr("app.services.symbols.fetch_listed_snapshot", fake_fetch)

    actual = fetch_latest_listed_snapshot(
        api_url="https://example.invalid/listed",
        api_key="synthetic-key",
        today=date(2026, 9, 7),
        lookback_days=5,
    )

    assert actual == expected
    assert attempted == [date(2026, 9, 6), date(2026, 9, 5), date(2026, 9, 4)]


def test_listed_info_mismatch_rejects_whole_sync() -> None:
    parsed = parse_symbol_csv(_csv("005930,삼성전자,KOSPI,보통주"))
    snapshot = ListedSnapshot(
        items=(),
        source_sha256="0" * 64,
        source_as_of=date(2026, 8, 29),
    )
    with pytest.raises(SymbolImportError, match="missing"):
        verify_listed_snapshot(parsed, snapshot)


def test_listed_info_accepts_official_name_variants() -> None:
    parsed = parse_symbol_csv(
        _csv(
            "005930,삼성전자,KOSPI,보통주",
            "383220,F&F,KOSPI,보통주",
        )
    )
    snapshot = ListedSnapshot(
        items=(
            ListedItem(
                code="005930",
                name_ko="삼성전자",
                market="KOSPI",
                source_as_of=date(2026, 8, 27),
            ),
            ListedItem(
                code="383220",
                name_ko="에프앤에프",
                market="KOSPI",
                source_as_of=date(2026, 8, 27),
            ),
        ),
        source_sha256="0" * 64,
        source_as_of=date(2026, 8, 27),
    )

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


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires migrated PostgreSQL",
)
async def test_daily_reconciliation_is_fail_closed_and_requires_two_missing_dates() -> None:
    csv_rows = tuple(f"{number:06d},테스트종목{number},KOSPI,보통주" for number in range(1, 101))
    parsed = parse_symbol_csv(_csv(*csv_rows))
    async with session_factory() as session, session.begin():
        await session.execute(delete(SymbolMasterVersion))

    try:
        async with session_factory() as session:
            baseline = await import_symbol_master(
                session,
                parsed,
                version="krx-reconciliation-baseline",
                source_as_of=date(2026, 9, 1),
            )

        def snapshot(source_as_of: date, source_hash: str, *, limit: int = 99) -> ListedSnapshot:
            items = tuple(
                ListedItem(
                    code=f"{number:06d}",
                    name_ko=f"테스트종목{number}",
                    market="KOSPI",
                    source_as_of=source_as_of,
                )
                for number in range(1, limit + 1)
            ) + (
                ListedItem(
                    code="999999",
                    name_ko="미분류신규종목",
                    market="KOSPI",
                    source_as_of=source_as_of,
                ),
            )
            return ListedSnapshot(
                items=items,
                source_sha256=source_hash * 64,
                source_as_of=source_as_of,
            )

        async with session_factory() as session:
            same_day = await reconcile_symbol_master(
                session,
                snapshot(date(2026, 9, 1), "0", limit=100),
            )
        assert same_day.created is False
        assert same_day.version_id == baseline.version_id

        async with session_factory() as session:
            first = await reconcile_symbol_master(
                session,
                snapshot(date(2026, 9, 2), "1"),
            )
        assert first.created is True
        assert first.row_count == 100
        assert first.pending_missing_count == 1
        assert first.removed_count == 0
        assert first.unknown_api_count == 1

        async with session_factory() as session:
            with pytest.raises(SymbolImportError, match="too many active symbols"):
                await reconcile_symbol_master(
                    session,
                    snapshot(date(2026, 9, 3), "2", limit=50),
                )
        async with session_factory() as session:
            still_active = await session.scalar(
                select(SymbolMasterVersion).where(SymbolMasterVersion.is_active.is_(True))
            )
            assert still_active is not None and still_active.id == first.version_id

        second_snapshot = snapshot(date(2026, 9, 3), "3")
        async with session_factory() as session:
            second = await reconcile_symbol_master(session, second_snapshot)
            replay = await reconcile_symbol_master(session, second_snapshot)
        assert second.created is True
        assert second.row_count == 99
        assert second.pending_missing_count == 0
        assert second.removed_count == 1
        assert replay.created is False
        assert replay.version_id == second.version_id

        async with session_factory() as session:
            active = await session.scalar(
                select(SymbolMasterVersion).where(SymbolMasterVersion.is_active.is_(True))
            )
            assert active is not None
            assert active.parent_version_id == first.version_id
            assert active.baseline_version_id == baseline.version_id
            assert active.source_kind == "LISTED_API_RECONCILIATION"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Symbol)
                    .where(Symbol.master_version_id == active.id)
                )
                == 99
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Symbol)
                    .where(
                        Symbol.master_version_id == active.id,
                        Symbol.code == "000100",
                    )
                )
                == 0
            )
            assert baseline.version_id != active.id

        async with session_factory() as session:
            reappeared = await reconcile_symbol_master(
                session,
                snapshot(date(2026, 9, 4), "4", limit=100),
            )
        assert reappeared.created is True
        assert reappeared.row_count == 100
        async with session_factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Symbol)
                    .where(
                        Symbol.master_version_id == reappeared.version_id,
                        Symbol.code == "000100",
                    )
                )
                == 1
            )
    finally:
        async with session_factory() as session, session.begin():
            version_ids = list(
                (
                    await session.scalars(
                        select(SymbolMasterVersion.id).order_by(
                            SymbolMasterVersion.source_as_of.desc()
                        )
                    )
                ).all()
            )
            for version_id in version_ids:
                await session.execute(delete(Symbol).where(Symbol.master_version_id == version_id))
                await session.execute(
                    delete(SymbolMasterVersion).where(SymbolMasterVersion.id == version_id)
                )
        await engine.dispose()
