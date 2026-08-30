import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
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
_IMPORT_LOCK_ID = 0x4D54534B5258
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
        raw_items = body.get("items", {}).get("item", [])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SymbolImportError("listed-info API returned an invalid response") from exc
    if result_code not in {"00", "0000"}:
        raise SymbolImportError("listed-info API rejected the request")
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

    page_no = 1
    seen_raw = 0
    pages: list[bytes] = []
    items: list[ListedItem] = []
    while True:
        query = urlencode(
            {
                "serviceKey": api_key,
                "resultType": "json",
                "pageNo": page_no,
                "numOfRows": page_size,
                "basDt": source_as_of.strftime("%Y%m%d"),
            }
        )
        request = Request(f"{api_url}?{query}", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except OSError as exc:
            raise SymbolImportError("listed-info API is unavailable") from exc
        page_items, total_count, page_item_count = _parse_listed_page(raw)
        pages.append(raw)
        items.extend(page_items)
        seen_raw += page_item_count
        if seen_raw >= total_count:
            break
        if page_item_count == 0:
            raise SymbolImportError("listed-info API pagination ended early")
        page_no += 1

    codes = [item.code for item in items]
    if not items or len(codes) != len(set(codes)):
        raise SymbolImportError("listed-info API target data is empty or duplicated")
    if any(item.source_as_of != source_as_of for item in items):
        raise SymbolImportError("listed-info API basis date does not match the requested date")
    return ListedSnapshot(
        items=tuple(items),
        source_sha256=hashlib.sha256(b"".join(pages)).hexdigest(),
        source_as_of=source_as_of,
    )


def verify_listed_snapshot(parsed: ParsedSymbolCsv, snapshot: ListedSnapshot) -> None:
    listed_by_code = {item.code: item for item in snapshot.items}
    for row in parsed.rows:
        listed = listed_by_code.get(row.code)
        if listed is None:
            raise SymbolImportError("CSV target symbol is missing from listed-info API")
        if listed.name_ko != row.name_ko or listed.market != row.market:
            raise SymbolImportError("CSV and listed-info API symbol metadata do not match")


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
