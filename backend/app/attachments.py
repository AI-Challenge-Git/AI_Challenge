import asyncio
import base64
import hashlib
import os
import re
import tempfile
import warnings
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import get_settings

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGE_PIXELS = 16_000_000
ALLOWED_FORMATS = {
    "PNG": ("image/png", "PNG"),
    "JPEG": ("image/jpeg", "JPEG"),
    "WEBP": ("image/webp", "WEBP"),
}
OBJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class InvalidAttachmentError(ValueError):
    pass


class AttachmentStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    content: bytes
    content_type: str
    sha256: str
    width: int
    height: int


def sanitize_attachment(raw: bytes) -> PreparedAttachment:
    if not raw or len(raw) > MAX_ATTACHMENT_BYTES:
        raise InvalidAttachmentError("attachment size is invalid")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=tuple(ALLOWED_FORMATS)) as probe:
                detected_format = probe.format
                probe.verify()
            with Image.open(BytesIO(raw), formats=tuple(ALLOWED_FORMATS)) as image:
                if detected_format not in ALLOWED_FORMATS or getattr(image, "n_frames", 1) != 1:
                    raise InvalidAttachmentError("attachment format is not allowed")
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise InvalidAttachmentError("attachment dimensions are invalid")
                image.load()
                has_alpha = "A" in image.getbands()
                clean = image.convert("RGBA" if has_alpha and detected_format != "JPEG" else "RGB")
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
    ) as exc:
        raise InvalidAttachmentError("attachment could not be decoded") from exc

    content_type, output_format = ALLOWED_FORMATS[detected_format]
    output = BytesIO()
    if output_format == "PNG":
        clean.save(output, format="PNG", optimize=True)
    elif output_format == "JPEG":
        clean.save(output, format="JPEG", quality=90, optimize=True, exif=b"", icc_profile=None)
    else:
        clean.save(
            output,
            format="WEBP",
            quality=90,
            method=4,
            exif=b"",
            icc_profile=None,
            xmp=b"",
        )
    content = output.getvalue()
    if not content or len(content) > MAX_ATTACHMENT_BYTES:
        raise InvalidAttachmentError("sanitized attachment size is invalid")
    return PreparedAttachment(
        content=content,
        content_type=content_type,
        sha256=hashlib.sha256(content).hexdigest(),
        width=width,
        height=height,
    )


class LocalAttachmentStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, object_key: str) -> Path:
        if not OBJECT_KEY_PATTERN.fullmatch(object_key):
            raise AttachmentStorageError("invalid attachment object key")
        return self.root / object_key

    async def put(self, object_key: str, content: bytes) -> None:
        path = self._path(object_key)

        def write() -> None:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                temporary_path.chmod(0o600)
                temporary_path.replace(path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(write)
        except OSError as exc:
            raise AttachmentStorageError("attachment write failed") from exc

    async def read(self, object_key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._path(object_key).read_bytes)
        except OSError as exc:
            raise AttachmentStorageError("attachment read failed") from exc

    async def delete(self, object_key: str) -> None:
        try:
            await asyncio.to_thread(self._path(object_key).unlink, missing_ok=True)
        except OSError as exc:
            raise AttachmentStorageError("attachment delete failed") from exc


@lru_cache
def get_attachment_store() -> LocalAttachmentStore:
    return LocalAttachmentStore(get_settings().attachment_storage_dir)


def attachment_data_url(content_type: str, content: bytes) -> str:
    return f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
