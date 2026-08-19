from collections.abc import Iterable, Iterator

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class JsonBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        multipart_max_bytes: int,
        paths: Iterable[str],
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.multipart_max_bytes = multipart_max_bytes
        self.paths = frozenset(paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return

        content_type = dict(scope["headers"]).get(b"content-type", b"").lower()
        is_multipart = content_type.startswith(b"multipart/form-data")
        max_bytes = self.multipart_max_bytes if is_multipart else self.max_bytes
        limit_label = "5 MiB 파일 제한" if is_multipart else "16 KiB"
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > max_bytes:
                response = JSONResponse(
                    status_code=413,
                    media_type="application/problem+json",
                    content={
                        "type": "about:blank",
                        "title": "요청 본문이 너무 큽니다.",
                        "status": 413,
                        "detail": f"요청 본문은 {limit_label} 이하여야 합니다.",
                        "code": "REQUEST_TOO_LARGE",
                    },
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        iterator: Iterator[Message] = iter(messages)

        async def replay() -> Message:
            return next(iterator, {"type": "http.disconnect"})

        await self.app(scope, replay, send)
