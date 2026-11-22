from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from businessflow.api.auth_routes import router as auth_router
from businessflow.api.company_context_routes import router as company_context_router
from businessflow.api.data_import_routes import router as data_import_router
from businessflow.api.financial_assessment_routes import router as financial_assessment_router
from businessflow.api.health_routes import router as health_router
from businessflow.api.metrics_routes import router as metrics_router
from businessflow.api.position_review_routes import router as position_review_router
from businessflow.api.project_assessment_routes import router as project_assessment_router
from businessflow.api.project_decision_routes import router as project_decision_router
from businessflow.api.tenant_routes import router as tenant_router
from businessflow.config import Settings, get_settings
from businessflow.database import create_database_engine, make_session_factory
from businessflow.errors import ApiError

logger = logging.getLogger("businessflow.api")


def _error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": str(request.state.request_id),
            }
        },
        headers={"X-Request-ID": str(request.state.request_id)},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    docs_enabled = active_settings.app_env != "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.engine.dispose()

    application = FastAPI(
        title="BusinessFlow AI API",
        version="0.1.0",
        description=(
            "Secure multi-tenant backend foundation. See the repository README for local setup."
        ),
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        debug=False,
        lifespan=lifespan,
    )
    engine = create_database_engine(
        active_settings.database_url,
        pool_size=active_settings.db_pool_size,
        max_overflow=active_settings.db_max_overflow,
    )
    application.state.settings = active_settings
    application.state.engine = engine
    application.state.session_factory = make_session_factory(engine)

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid4()
        request.state.now = datetime.now(UTC)
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; object-src 'none'; "
                "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
                "style-src 'self'; script-src 'self'; connect-src 'self'"
            )
        return response

    @application.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(request, exc.status_code, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        # Do not echo body values: validation failures may contain passwords or bearer tokens.
        return _error_response(request, 422, "invalid_request", "Request validation failed.")

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(
                request, 404, "not_found", "The requested resource was not found."
            )
        if exc.status_code == 405:
            return _error_response(request, 405, "method_not_allowed", "The method is not allowed.")
        return _error_response(
            request, exc.status_code, "request_failed", "The request could not be completed."
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "request %s failed with unhandled exception type %s",
            request.state.request_id,
            type(exc).__name__,
        )
        return _error_response(
            request, 500, "internal_error", "The request could not be completed."
        )

    application.include_router(health_router)
    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(tenant_router, prefix="/api/v1")
    application.include_router(company_context_router, prefix="/api/v1")
    application.include_router(data_import_router, prefix="/api/v1")
    application.include_router(metrics_router, prefix="/api/v1")
    application.include_router(project_assessment_router, prefix="/api/v1")
    application.include_router(project_decision_router, prefix="/api/v1")
    application.include_router(financial_assessment_router, prefix="/api/v1")
    application.include_router(position_review_router, prefix="/api/v1")
    application.mount(
        "/",
        StaticFiles(directory=Path(__file__).resolve().parent / "web", html=True),
        name="web",
    )
    return application


app = create_app()
