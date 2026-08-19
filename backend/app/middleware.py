from collections.abc import Iterable, Iterator

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class JsonBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int, paths: Iterable[str]) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.paths = frozenset(paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return

        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    media_type="application/problem+json",
                    content={
                        "type": "about:blank",
                        "title": "요청 본문이 너무 큽니다.",
                        "status": 413,
                        "detail": "요청 본문은 16 KiB 이하여야 합니다.",
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
