from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agents import router as agents_router
from app.api.health import router as health_router
from app.api.reports import router as reports_router
from app.config import Settings, get_settings
from app.db import engine
from app.errors import ServiceError
from app.middleware import JsonBodyLimitMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    application.add_middleware(
        JsonBodyLimitMiddleware,
        max_bytes=16 * 1024,
        multipart_max_bytes=5 * 1024 * 1024 + 64 * 1024,
        paths={"/api/reports/analyze"},
    )

    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Accept", "Authorization", "Content-Type"],
        )

    @application.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
        headers = {"Cache-Control": "no-store", **exc.headers}
        return JSONResponse(
            status_code=exc.status,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "요청을 처리하지 못했습니다.",
                "status": exc.status,
                "detail": exc.detail,
                "code": exc.code,
            },
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": ".".join(str(part) for part in error["loc"]),
                "msg": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "요청 값이 올바르지 않습니다.",
                "status": 422,
                "detail": "요청 값을 확인해 주세요.",
                "code": "VALIDATION_ERROR",
                "errors": errors,
            },
            headers={"Cache-Control": "no-store"},
        )

    application.include_router(health_router)
    application.include_router(agents_router)
    application.include_router(reports_router)
    return application


app = create_app()
