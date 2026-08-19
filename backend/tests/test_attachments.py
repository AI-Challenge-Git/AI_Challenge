from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from app.attachments import InvalidAttachmentError, sanitize_attachment


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
