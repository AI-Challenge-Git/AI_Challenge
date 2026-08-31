import asyncio
from urllib.request import Request, urlopen

from app.attachments import get_attachment_store
from app.config import get_settings
from app.security import make_opaque_token

_SMOKE_CONTENT = b"mts-sos-private-storage-smoke-v1"


async def run() -> None:
    settings = get_settings()
    if settings.attachment_storage_backend != "s3":
        raise SystemExit("ATTACHMENT_STORAGE_BACKEND=s3 is required")

    store = get_attachment_store()
    object_key = make_opaque_token()
    try:
        await store.put(object_key, _SMOKE_CONTENT)
        if await store.read(object_key) != _SMOKE_CONTENT:
            raise RuntimeError("private object storage read verification failed")
        signed_url = store.signed_get_url(
            object_key,
            content_type="application/octet-stream",
            expires_in=settings.attachment_signed_url_ttl_seconds,
        )
        if signed_url is None:
            raise RuntimeError("private object storage did not create a signed URL")

        def signed_get() -> bytes:
            request = Request(signed_url, headers={"Accept": "application/octet-stream"})
            with urlopen(request, timeout=30) as response:  # noqa: S310
                return response.read()

        if await asyncio.to_thread(signed_get) != _SMOKE_CONTENT:
            raise RuntimeError("signed object storage read verification failed")
    finally:
        await store.delete(object_key)
    print("object_storage_smoke=passed uploaded=true signed_get=true deleted=true")


if __name__ == "__main__":
    asyncio.run(run())
