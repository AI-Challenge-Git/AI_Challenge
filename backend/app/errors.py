class ServiceError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers or {}
