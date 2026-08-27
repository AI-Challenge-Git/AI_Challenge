import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ServiceError
from app.models import Symbol, SymbolMasterVersion

KRX_SOURCE_URL = "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"
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
