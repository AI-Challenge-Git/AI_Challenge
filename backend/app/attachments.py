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
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
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


class AttachmentStore(Protocol):
    async def put(self, object_key: str, content: bytes) -> None: ...

    async def read(self, object_key: str) -> bytes: ...

    async def delete(self, object_key: str) -> None: ...

    def signed_get_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_in: int,
    ) -> str | None: ...


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

    def signed_get_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_in: int,
    ) -> str | None:
        self._path(object_key)
        return None


class S3AttachmentStore:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        addressing_style: str,
    ) -> None:
        self.bucket = bucket
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(s3={"addressing_style": addressing_style}, signature_version="s3v4"),
        )

    @staticmethod
    def _validate_key(object_key: str) -> None:
        if not OBJECT_KEY_PATTERN.fullmatch(object_key):
            raise AttachmentStorageError("invalid attachment object key")

    async def put(self, object_key: str, content: bytes) -> None:
        self._validate_key(object_key)
        try:
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=object_key,
                Body=content,
            )
        except Exception as exc:
            raise AttachmentStorageError("attachment write failed") from exc

    async def read(self, object_key: str) -> bytes:
        self._validate_key(object_key)
        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket,
                Key=object_key,
            )
            return bytes(await asyncio.to_thread(response["Body"].read))
        except Exception as exc:
            raise AttachmentStorageError("attachment read failed") from exc

    async def delete(self, object_key: str) -> None:
        self._validate_key(object_key)
        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket,
                Key=object_key,
            )
        except Exception as exc:
            raise AttachmentStorageError("attachment delete failed") from exc

    def signed_get_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_in: int,
    ) -> str:
        self._validate_key(object_key)
        try:
            return str(
                self.client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self.bucket,
                        "Key": object_key,
                        "ResponseContentType": content_type,
                        "ResponseCacheControl": "no-store",
                    },
                    ExpiresIn=expires_in,
                )
            )
        except Exception as exc:
            raise AttachmentStorageError("attachment URL signing failed") from exc


@lru_cache
def get_attachment_store() -> AttachmentStore:
    settings = get_settings()
    if settings.attachment_storage_backend == "local":
        return LocalAttachmentStore(settings.attachment_storage_dir)
    if not all(
        (
            settings.bucket,
            settings.access_key_id,
            settings.secret_access_key,
            settings.region,
            settings.endpoint,
        )
    ):
        raise AttachmentStorageError("private object storage is not configured")
    return S3AttachmentStore(
        bucket=settings.bucket or "",
        endpoint=settings.endpoint or "",
        region=settings.region or "",
        access_key_id=(settings.access_key_id.get_secret_value() if settings.access_key_id else ""),
        secret_access_key=(
            settings.secret_access_key.get_secret_value() if settings.secret_access_key else ""
        ),
        addressing_style=settings.s3_addressing_style,
    )


def attachment_data_url(content_type: str, content: bytes) -> str:
    return f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
