from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import admin, routes
from app.auth.identity import get_verifier
from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import AppError
from app.schemas import HealthOut

log = logging.getLogger("consentbridge.http")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
    )


def _error(status: int, code: str, message: str, request: Request, details: dict | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message,
                            "correlation_id": getattr(request.state, "correlation_id", None)}}
    if details:
        body["error"]["details"] = details
    headers = {}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="consentbridge"'
    if status == 429 and details and "retry_after_seconds" in details:
        headers["Retry-After"] = str(details["retry_after_seconds"])
    return JSONResponse(status_code=status, content=body, headers=headers)


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)
    docs = settings.environment != "production"
    app = FastAPI(
        title="ConsentBridge API",
        version="1.0.0",
        description="Privacy-first healthcare data sharing. Authentication is necessary, never sufficient.",
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def correlation_and_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]
                                      ) -> Response:
        cid = request.headers.get("x-correlation-id", "")
        if not (0 < len(cid) <= 64 and cid.replace("-", "").isalnum()):
            cid = uuid.uuid4().hex
        request.state.correlation_id = cid
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        route = request.scope.get("route")
        path_template = getattr(route, "path", "unmatched")
        # Method, route template, status and timing only: no query strings, bodies or headers.
        log.info("request method=%s route=%s status=%s ms=%.1f cid=%s user=%s", request.method, path_template,
                 response.status_code, elapsed, cid, getattr(request.state, "user_id", "-"))
        response.headers["X-Correlation-Id"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=False,  # bearer tokens, no cookies
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Authorization", "Content-Type", "X-Correlation-Id"],
            max_age=600,
        )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, request, exc.extra or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Report location and message only; never echo submitted input values.
        errors = [{"loc": [str(p) for p in e.get("loc", ())][-3:], "msg": str(e.get("msg", ""))[:200]}
                  for e in exc.errors()][:20]
        return _error(422, "VALIDATION_FAILED", "The request is invalid.", request, {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, "Not found." if exc.status_code == 404 else "Request not allowed.",
                      request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error type=%s cid=%s", type(exc).__name__, getattr(request.state, "correlation_id", "-"))
        return _error(500, "INTERNAL_ERROR", "An unexpected error occurred. Nothing was released.", request)

    @app.get("/healthz", response_model=HealthOut, tags=["health"])
    def healthz() -> HealthOut:
        return HealthOut(status="ok", checks={})

    @app.get("/readyz", response_model=HealthOut, tags=["health"])
    def readyz() -> JSONResponse:
        checks: dict = {}
        ok = True
        try:
            with get_sessionmaker()() as s:
                s.execute(text("SELECT 1"))
                active = s.execute(text("SELECT version FROM policy_versions WHERE is_active")).scalar()
            checks["database"] = "ok"
            checks["policy_version"] = active
            ok = ok and active is not None
        except Exception:  # noqa: BLE001
            checks["database"] = "unavailable"
            ok = False
        status = get_verifier().status()
        checks["auth_configured"] = status["configured"]
        checks["auth_key_source"] = status["key_source"]
        ok = ok and bool(status["configured"])
        body = HealthOut(status="ok" if ok else "degraded", checks=checks)
        return JSONResponse(status_code=200 if ok else 503, content=body.model_dump())

    app.include_router(routes.router)
    app.include_router(admin.router)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def unknown_api(path: str) -> None:
        raise AppError(404, "NOT_FOUND", "Unknown API endpoint.")

    return app


app = create_app()
