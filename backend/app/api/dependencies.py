from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.errors import ServiceError
from app.security import InvalidSessionTokenError, session_digest

bearer = HTTPBearer(auto_error=False)


def customer_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> bytes:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ServiceError(401, "AUTH_REQUIRED", "인증 정보가 필요합니다.")
    try:
        return session_digest(
            credentials.credentials,
            settings.session_hmac_key.get_secret_value().encode(),
        )
    except (InvalidSessionTokenError, ValueError) as exc:
        raise ServiceError(401, "INVALID_SESSION", "인증 정보가 올바르지 않습니다.") from exc
