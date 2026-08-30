from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from app.attachments import InvalidAttachmentError, S3AttachmentStore, sanitize_attachment


def _png_bytes(*, width: int = 8, height: int = 8, metadata: bool = False) -> bytes:
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text("account", "123-456-789")
    Image.new("RGB", (width, height), "navy").save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def test_sanitizer_decodes_image_and_strips_metadata() -> None:
    prepared = sanitize_attachment(_png_bytes(metadata=True))

    assert prepared.content_type == "image/png"
    assert prepared.width == prepared.height == 8
    assert b"123-456-789" not in prepared.content
    with Image.open(BytesIO(prepared.content)) as image:
        assert image.info == {}


@pytest.mark.parametrize(
    "raw",
    [b"", b"not-an-image", b"x" * (5 * 1024 * 1024 + 1)],
    ids=["empty", "malformed", "oversized"],
)
def test_sanitizer_rejects_invalid_or_oversized_files(raw: bytes) -> None:
    with pytest.raises(InvalidAttachmentError):
        sanitize_attachment(raw)


def test_sanitizer_rejects_oversized_dimensions() -> None:
    with pytest.raises(InvalidAttachmentError):
        sanitize_attachment(_png_bytes(width=4097, height=1))


class _FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        self.puts.append(kwargs)

    def delete_object(self, **kwargs: object) -> None:
        self.deletes.append(kwargs)

    def generate_presigned_url(self, operation: str, **kwargs: object) -> str:
        assert operation == "get_object"
        assert kwargs["ExpiresIn"] == 300
        return "https://private.example.invalid/signed"


async def test_s3_store_uses_private_object_operations_and_short_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3Client()
    monkeypatch.setattr("app.attachments.boto3.client", lambda *_args, **_kwargs: client)
    store = S3AttachmentStore(
        bucket="private-bucket",
        endpoint="https://storage.example.invalid",
        region="auto",
        access_key_id="synthetic-id",
        secret_access_key="synthetic-secret",
        addressing_style="virtual",
    )
    object_key = "A" * 43

    await store.put(object_key, b"image")
    url = store.signed_get_url(object_key, content_type="image/png", expires_in=300)
    await store.delete(object_key)

    assert url == "https://private.example.invalid/signed"
    assert client.puts == [{"Bucket": "private-bucket", "Key": object_key, "Body": b"image"}]
    assert client.deletes == [{"Bucket": "private-bucket", "Key": object_key}]
