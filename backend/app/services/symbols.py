import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ServiceError
from app.models import Symbol, SymbolMasterVersion

KRX_SOURCE_URL = "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"
KRX_LISTED_INFO_API_URL = (
    "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
)
SYMBOL_SCHEMA_VERSION = "krx-all-symbols.v1"
RECONCILED_SYMBOL_SCHEMA_VERSION = "krx-common-allowlist-reconcile.v1"
CSV_SOURCE_KIND = "KRX_CSV"
RECONCILED_SOURCE_KIND = "LISTED_API_RECONCILIATION"
_IMPORT_LOCK_ID = 0x4D54534B5258
_MIN_LISTED_CODE_COVERAGE = 0.99
_MAX_LISTED_API_PAGES = 100
_MAX_LISTED_API_PAGE_BYTES = 5 * 1024 * 1024
_CODE_PATTERN = re.compile(r"^[0-9A-Z]{6}$")
_TARGET_MARKETS = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    "KOSDAQ GLOBAL": "KOSDAQ",
}
_REQUIRED_COLUMNS = {
    "단축코드",
    "한글 종목약명",
    "시장구분",
    "주식종류",
}


class SymbolImportError(ValueError):
    pass


class ListedSnapshotNotFound(SymbolImportError):
    pass


@dataclass(frozen=True, slots=True)
class SymbolRow:
    code: str
    name_ko: str
    market: str
    source_market: str
    stock_type: str


@dataclass(frozen=True, slots=True)
class ParsedSymbolCsv:
    rows: tuple[SymbolRow, ...]
    source_sha256: str
    source_encoding: str


@dataclass(frozen=True, slots=True)
class SymbolImportResult:
    version_id: UUID
    version: str
    row_count: int
    created: bool


@dataclass(frozen=True, slots=True)
class SymbolReconciliationResult:
    version_id: UUID
    version: str
    source_as_of: date
    row_count: int
    pending_missing_count: int
    removed_count: int
    unknown_api_count: int
    name_change_count: int
    created: bool


@dataclass(frozen=True, slots=True)
class ListedItem:
    code: str
    name_ko: str
    market: str
    source_as_of: date


@dataclass(frozen=True, slots=True)
class ListedSnapshot:
    items: tuple[ListedItem, ...]
    source_sha256: str
    source_as_of: date


def _parse_listed_page(raw: bytes) -> tuple[list[ListedItem], int, int]:
    try:
        payload: Any = json.loads(raw)
        response = payload["response"]
        header = response["header"]
        body = response["body"]
        result_code = str(header["resultCode"])
        total_count = int(body["totalCount"])
        raw_items_container = body.get("items")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SymbolImportError("listed-info API returned an invalid response") from exc
    if result_code not in {"00", "0000"}:
        raise SymbolImportError("listed-info API rejected the request")
    if total_count < 0:
        raise SymbolImportError("listed-info API total count is invalid")
    if raw_items_container in (None, ""):
        raw_items: Any = []
    elif isinstance(raw_items_container, dict):
        raw_items = raw_items_container.get("item", [])
    else:
        raise SymbolImportError("listed-info API items are invalid")
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        raise SymbolImportError("listed-info API items are invalid")
    raw_item_count = len(raw_items)

    items: list[ListedItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise SymbolImportError("listed-info API item is invalid")
        market = unicodedata.normalize("NFC", str(item.get("mrktCtg", "")).strip())
        if market not in {"KOSPI", "KOSDAQ"}:
            continue
        source_code = str(item.get("srtnCd", "")).strip().upper()
        code = (
            source_code[1:]
            if len(source_code) == 7 and source_code.startswith("A")
            else source_code
        )
        name_ko = unicodedata.normalize("NFC", str(item.get("itmsNm", "")).strip())
        bas_dt = str(item.get("basDt", "")).strip()
        if not _CODE_PATTERN.fullmatch(code) or not name_ko:
            raise SymbolImportError("listed-info API contains invalid target data")
        try:
            source_as_of = date.fromisoformat(f"{bas_dt[:4]}-{bas_dt[4:6]}-{bas_dt[6:8]}")
        except ValueError as exc:
            raise SymbolImportError("listed-info API basis date is invalid") from exc
        items.append(
            ListedItem(
                code=code,
                name_ko=name_ko,
                market=market,
                source_as_of=source_as_of,
            )
        )
    return items, total_count, raw_item_count


def fetch_listed_snapshot(
    *,
    api_url: str,
    api_key: str,
    source_as_of: date,
    page_size: int = 1000,
    timeout_seconds: float = 30.0,
) -> ListedSnapshot:
    if not api_key.strip():
        raise SymbolImportError("KRX listed-info API key is not configured")
    if not 1 <= page_size <= 10_000:
        raise SymbolImportError("listed-info API page size is invalid")
    parsed_url = urlsplit(api_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise SymbolImportError("listed-info API URL must be an HTTPS endpoint without a query")

    page_no = 1
    seen_raw = 0
    expected_total: int | None = None
    items: list[ListedItem] = []
    while True:
        query = urlencode(
            {
                "resultType": "json",
                "pageNo": page_no,
                "numOfRows": page_size,
                "basDt": source_as_of.strftime("%Y%m%d"),
            }
        )
        encoded_key = quote(api_key.strip(), safe="%")
        request = Request(
            f"{api_url}?serviceKey={encoded_key}&{query}",
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read(_MAX_LISTED_API_PAGE_BYTES + 1)
        except OSError as exc:
            raise SymbolImportError("listed-info API is unavailable") from exc
        if len(raw) > _MAX_LISTED_API_PAGE_BYTES:
            raise SymbolImportError("listed-info API response is too large")
        page_items, total_count, page_item_count = _parse_listed_page(raw)
        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise SymbolImportError("listed-info API total count changed during pagination")
        items.extend(page_items)
        seen_raw += page_item_count
        if seen_raw >= total_count:
            break
        if page_item_count == 0:
            raise SymbolImportError("listed-info API pagination ended early")
        page_no += 1
        if page_no > _MAX_LISTED_API_PAGES:
            raise SymbolImportError("listed-info API pagination exceeds the safe limit")

    codes = [item.code for item in items]
    if not items:
        raise ListedSnapshotNotFound("listed-info API has no target data for the requested date")
    if len(codes) != len(set(codes)):
        raise SymbolImportError("listed-info API target data is duplicated")
    if any(item.source_as_of != source_as_of for item in items):
        raise SymbolImportError("listed-info API basis date does not match the requested date")
    sorted_items = tuple(sorted(items, key=lambda item: item.code))
    canonical = json.dumps(
        [
            {
                "code": item.code,
                "market": item.market,
                "name_ko": item.name_ko,
                "source_as_of": item.source_as_of.isoformat(),
            }
            for item in sorted_items
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ListedSnapshot(
        items=sorted_items,
        source_sha256=hashlib.sha256(canonical).hexdigest(),
        source_as_of=source_as_of,
    )


def fetch_latest_listed_snapshot(
    *,
    api_url: str,
    api_key: str,
    today: date,
    lookback_days: int = 14,
) -> ListedSnapshot:
    if not 1 <= lookback_days <= 31:
        raise SymbolImportError("listed-info API lookback must be between 1 and 31 days")
    for days_ago in range(1, lookback_days + 1):
        source_as_of = today - timedelta(days=days_ago)
        try:
            return fetch_listed_snapshot(
                api_url=api_url,
                api_key=api_key,
                source_as_of=source_as_of,
            )
        except ListedSnapshotNotFound:
            continue
    raise SymbolImportError("listed-info API has no recent target snapshot")


def verify_listed_snapshot(parsed: ParsedSymbolCsv, snapshot: ListedSnapshot) -> None:
    listed_by_code = {item.code: item for item in snapshot.items}
    matched = 0
    for row in parsed.rows:
        listed = listed_by_code.get(row.code)
        if listed is None:
            continue
        matched += 1
        if listed.market != row.market:
            raise SymbolImportError("CSV and listed-info API markets do not match")
    if matched / len(parsed.rows) < _MIN_LISTED_CODE_COVERAGE:
        raise SymbolImportError("too many CSV target symbols are missing from listed-info API")


def _decode_csv(raw: bytes) -> tuple[str, str]:
    for encoding, label in (("utf-8-sig", "UTF-8-SIG"), ("cp949", "CP949")):
        try:
            return raw.decode(encoding), label
        except UnicodeDecodeError:
            continue
    raise SymbolImportError("CSV encoding must be UTF-8-SIG or CP949")


def parse_symbol_csv(raw: bytes) -> ParsedSymbolCsv:
    text, encoding = _decode_csv(raw)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = reader.fieldnames
    if fieldnames is None or len(fieldnames) != len(set(fieldnames)):
        raise SymbolImportError("CSV header is missing or duplicated")
    missing = _REQUIRED_COLUMNS.difference(fieldnames)
    if missing:
        raise SymbolImportError("CSV header is missing required columns")

    rows: list[SymbolRow] = []
    codes: set[str] = set()
    for source_row in reader:
        source_market = unicodedata.normalize("NFC", (source_row.get("시장구분") or "").strip())
        stock_type = unicodedata.normalize("NFC", (source_row.get("주식종류") or "").strip())
        market = _TARGET_MARKETS.get(source_market)
        if market is None or stock_type != "보통주":
            continue

        code = (source_row.get("단축코드") or "").strip()
        name_ko = unicodedata.normalize("NFC", (source_row.get("한글 종목약명") or "").strip())
        if not _CODE_PATTERN.fullmatch(code):
            raise SymbolImportError(
                "target symbol code must contain exactly six uppercase letters or digits"
            )
        if not name_ko or len(name_ko) > 80:
            raise SymbolImportError("target symbol name must not be blank or too long")
        if code in codes:
            raise SymbolImportError("target symbol code is duplicated")
        codes.add(code)
        rows.append(
            SymbolRow(
                code=code,
                name_ko=name_ko,
                market=market,
                source_market=source_market,
                stock_type=stock_type,
            )
        )

    if not rows:
        raise SymbolImportError("CSV contains no supported common stocks")
    return ParsedSymbolCsv(
        rows=tuple(rows),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_encoding=encoding,
    )


async def import_symbol_master(
    session: AsyncSession,
    parsed: ParsedSymbolCsv,
    *,
    version: str,
    source_as_of: date,
    source_url: str = KRX_SOURCE_URL,
) -> SymbolImportResult:
    normalized_version = unicodedata.normalize("NFC", version.strip())
    normalized_url = source_url.strip()
    if not normalized_version or len(normalized_version) > 64:
        raise SymbolImportError("version must contain 1 to 64 characters")
    if not normalized_url:
        raise SymbolImportError("source URL must not be blank")

    async with session.begin():
        await session.execute(select(func.pg_advisory_xact_lock(_IMPORT_LOCK_ID)))
        existing = await session.scalar(
            select(SymbolMasterVersion)
            .where(
                or_(
                    SymbolMasterVersion.version == normalized_version,
                    SymbolMasterVersion.source_sha256 == parsed.source_sha256,
                )
            )
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.version == normalized_version
                and existing.source_sha256 == parsed.source_sha256
                and existing.source_as_of == source_as_of
                and existing.source_url == normalized_url
                and existing.schema_version == SYMBOL_SCHEMA_VERSION
                and existing.row_count == len(parsed.rows)
                and existing.is_active
            ):
                return SymbolImportResult(
                    version_id=existing.id,
                    version=existing.version,
                    row_count=existing.row_count,
                    created=False,
                )
            raise SymbolImportError("version or source hash was already imported")

        await session.execute(
            update(SymbolMasterVersion)
            .where(SymbolMasterVersion.is_active.is_(True))
            .values(is_active=False)
        )
        master = SymbolMasterVersion(
            version=normalized_version,
            source_url=normalized_url,
            source_as_of=source_as_of,
            source_sha256=parsed.source_sha256,
            source_encoding=parsed.source_encoding,
            source_kind=CSV_SOURCE_KIND,
            schema_version=SYMBOL_SCHEMA_VERSION,
            row_count=len(parsed.rows),
            is_active=True,
        )
        session.add(master)
        await session.flush()
        session.add_all(
            Symbol(
                master_version_id=master.id,
                code=row.code,
                name_ko=row.name_ko,
                market=row.market,
                source_market=row.source_market,
                stock_type=row.stock_type,
            )
            for row in parsed.rows
        )
        await session.flush()
        return SymbolImportResult(
            version_id=master.id,
            version=master.version,
            row_count=master.row_count,
            created=True,
        )


async def reconcile_symbol_master(
    session: AsyncSession,
    snapshot: ListedSnapshot,
    *,
    source_url: str = KRX_LISTED_INFO_API_URL,
) -> SymbolReconciliationResult:
    normalized_url = source_url.strip()
    if not normalized_url:
        raise SymbolImportError("source URL must not be blank")
    version = f"krx-listed-reconcile-{snapshot.source_as_of.isoformat()}"

    async with session.begin():
        await session.execute(select(func.pg_advisory_xact_lock(_IMPORT_LOCK_ID)))
        active = await session.scalar(
            select(SymbolMasterVersion)
            .where(SymbolMasterVersion.is_active.is_(True))
            .with_for_update()
        )
        if active is None:
            raise SymbolImportError("an active CSV-based Symbol Master is required")
        if snapshot.source_as_of < active.source_as_of:
            raise SymbolImportError(
                "listed-info API snapshot is older than the active Symbol Master"
            )

        active_symbols = list(
            (
                await session.scalars(
                    select(Symbol)
                    .where(Symbol.master_version_id == active.id)
                    .order_by(Symbol.code)
                )
            ).all()
        )
        if not active_symbols:
            raise SymbolImportError("active Symbol Master contains no symbols")
        baseline_version_id = active.baseline_version_id or active.id
        baseline_symbols = list(
            (
                await session.scalars(
                    select(Symbol)
                    .where(Symbol.master_version_id == baseline_version_id)
                    .order_by(Symbol.code)
                )
            ).all()
        )
        if not baseline_symbols:
            raise SymbolImportError("CSV baseline Symbol Master contains no symbols")
        listed_by_code = {item.code: item for item in snapshot.items}
        active_codes = {symbol.code for symbol in active_symbols}
        baseline_codes = {symbol.code for symbol in baseline_symbols}
        matched_count = len(active_codes.intersection(listed_by_code))
        if matched_count / len(active_symbols) < _MIN_LISTED_CODE_COVERAGE:
            raise SymbolImportError("too many active symbols are missing from listed-info API")
        for symbol in baseline_symbols:
            listed = listed_by_code.get(symbol.code)
            if listed is not None and listed.market != symbol.market:
                raise SymbolImportError("Symbol Master and listed-info API markets do not match")
        unknown_api_count = len(set(listed_by_code).difference(baseline_codes))
        name_change_count = sum(
            listed_by_code[symbol.code].name_ko != symbol.name_ko
            for symbol in baseline_symbols
            if symbol.code in listed_by_code
        )
        pending_missing_count = sum(
            symbol.listed_api_missing_since is not None for symbol in active_symbols
        )

        existing = await session.scalar(
            select(SymbolMasterVersion).where(
                or_(
                    SymbolMasterVersion.version == version,
                    SymbolMasterVersion.source_sha256 == snapshot.source_sha256,
                )
            )
        )
        if existing is not None:
            if (
                existing.id == active.id
                and existing.version == version
                and existing.source_sha256 == snapshot.source_sha256
                and existing.source_as_of == snapshot.source_as_of
                and existing.source_kind == RECONCILED_SOURCE_KIND
            ):
                return SymbolReconciliationResult(
                    version_id=active.id,
                    version=active.version,
                    source_as_of=active.source_as_of,
                    row_count=active.row_count,
                    pending_missing_count=pending_missing_count,
                    removed_count=0,
                    unknown_api_count=unknown_api_count,
                    name_change_count=name_change_count,
                    created=False,
                )
            raise SymbolImportError("snapshot version or source hash was already imported")
        if snapshot.source_as_of == active.source_as_of:
            if active.source_kind == CSV_SOURCE_KIND:
                return SymbolReconciliationResult(
                    version_id=active.id,
                    version=active.version,
                    source_as_of=active.source_as_of,
                    row_count=active.row_count,
                    pending_missing_count=pending_missing_count,
                    removed_count=0,
                    unknown_api_count=unknown_api_count,
                    name_change_count=name_change_count,
                    created=False,
                )
            raise SymbolImportError("active Symbol Master already uses this basis date")

        next_symbols: list[dict[str, Any]] = []
        pending_missing_count = 0
        removed_count = 0
        active_by_code = {symbol.code: symbol for symbol in active_symbols}
        for baseline_symbol in baseline_symbols:
            current_symbol = active_by_code.get(baseline_symbol.code)
            listed = listed_by_code.get(baseline_symbol.code)
            last_seen_on: date | None
            missing_since: date | None
            if listed is not None:
                last_seen_on = snapshot.source_as_of
                missing_since = None
            elif current_symbol is None:
                continue
            elif (
                current_symbol.listed_api_missing_since is not None
                and current_symbol.listed_api_missing_since < snapshot.source_as_of
            ):
                removed_count += 1
                continue
            else:
                last_seen_on = current_symbol.listed_api_last_seen_on
                missing_since = snapshot.source_as_of
                pending_missing_count += 1

            next_symbols.append(
                {
                    "code": baseline_symbol.code,
                    "name_ko": baseline_symbol.name_ko,
                    "market": baseline_symbol.market,
                    "source_market": baseline_symbol.source_market,
                    "stock_type": baseline_symbol.stock_type,
                    "listed_api_last_seen_on": last_seen_on,
                    "listed_api_missing_since": missing_since,
                }
            )
        if not next_symbols:
            raise SymbolImportError("reconciliation would empty the Symbol Master")

        await session.execute(
            update(SymbolMasterVersion)
            .where(SymbolMasterVersion.is_active.is_(True))
            .values(is_active=False)
        )
        master = SymbolMasterVersion(
            version=version,
            source_url=normalized_url,
            source_as_of=snapshot.source_as_of,
            source_sha256=snapshot.source_sha256,
            source_encoding="API-JSON",
            source_kind=RECONCILED_SOURCE_KIND,
            parent_version_id=active.id,
            baseline_version_id=baseline_version_id,
            schema_version=RECONCILED_SYMBOL_SCHEMA_VERSION,
            row_count=len(next_symbols),
            is_active=True,
        )
        session.add(master)
        await session.flush()
        session.add_all(
            Symbol(master_version_id=master.id, **symbol_values) for symbol_values in next_symbols
        )
        await session.flush()
        return SymbolReconciliationResult(
            version_id=master.id,
            version=master.version,
            source_as_of=master.source_as_of,
            row_count=master.row_count,
            pending_missing_count=pending_missing_count,
            removed_count=removed_count,
            unknown_api_count=unknown_api_count,
            name_change_count=name_change_count,
            created=True,
        )


async def validate_symbol(
    session: AsyncSession,
    *,
    symbol_name: str | None,
    symbol_code: str | None,
) -> UUID | None:
    if symbol_code in (None, "UNKNOWN"):
        return None

    master = await session.scalar(
        select(SymbolMasterVersion).where(SymbolMasterVersion.is_active.is_(True))
    )
    if master is None:
        raise ServiceError(
            503,
            "SYMBOL_MASTER_UNAVAILABLE",
            "종목 기준정보를 사용할 수 없습니다.",
        )
    symbol = await session.scalar(
        select(Symbol).where(
            Symbol.master_version_id == master.id,
            Symbol.code == symbol_code,
        )
    )
    if symbol is None:
        raise ServiceError(422, "UNSUPPORTED_SYMBOL", "지원하지 않는 종목코드입니다.")
    if symbol_name not in (None, "UNKNOWN") and symbol.name_ko != symbol_name:
        raise ServiceError(422, "SYMBOL_MISMATCH", "종목명과 종목코드가 일치하지 않습니다.")
    return master.id


def load_symbol_csv(path: Path) -> ParsedSymbolCsv:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SymbolImportError("CSV file could not be read") from exc
    return parse_symbol_csv(raw)
