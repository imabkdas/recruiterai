"""FastAPI application factory for JobPilot."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from jobpilot.api.routes import analytics, answers, health, jobs, progress, run
from jobpilot.api.run_state import (
    RunAlreadyInProgressError,
    RunConflict,
    RunStateManager,
)
from jobpilot.api.security import (
    configure_security,
    generate_or_get_token,
    inject_token_into_html,
)
from jobpilot.config import AppConfig, load_config
from jobpilot.tracking.status import InvalidStatusTransitionError

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


def create_app(
    config: AppConfig | None = None,
    dev: bool = False,
    token: str | None = None,
    run_state_manager: RunStateManager | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application for JobPilot."""
    cfg = config or load_config()
    api_token = token or generate_or_get_token(dev=dev)
    state_manager = run_state_manager or RunStateManager()

    app = FastAPI(
        title="JobPilot API",
        description="Local JSON API for JobPilot search and application management.",
        version="0.1.0",
    )

    # Store state on application object
    app.state.config = cfg
    app.state.dev = dev
    app.state.token = api_token
    app.state.run_state = state_manager

    # -----------------------------------------------------------------------
    # Error Handlers per Requirement 5
    # -----------------------------------------------------------------------

    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        detail = str(exc.args[0]) if exc.args else str(exc)
        return JSONResponse(
            status_code=404,
            content={"detail": detail.strip("'\""), "code": "not_found"},
        )

    @app.exception_handler(InvalidStatusTransitionError)
    async def transition_error_handler(
        request: Request, exc: InvalidStatusTransitionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "code": "invalid_status_transition"},
        )

    @app.exception_handler(RunAlreadyInProgressError)
    @app.exception_handler(RunConflict)
    async def run_conflict_handler(
        request: Request, exc: RunAlreadyInProgressError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "code": "run_in_progress"},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "validation_error"},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        if isinstance(exc, InvalidStatusTransitionError):
            return JSONResponse(
                status_code=409,
                content={"detail": str(exc), "code": "invalid_status_transition"},
            )
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "validation_error"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = (
            "not_found"
            if exc.status_code == 404
            else ("forbidden" if exc.status_code == 403 else "http_error")
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc.detail), "code": code},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Internal server error")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "internal_error"},
        )

    # -----------------------------------------------------------------------
    # Security Middleware per Requirement 6
    # -----------------------------------------------------------------------
    configure_security(app, token=api_token, dev=dev)

    # -----------------------------------------------------------------------
    # API Routers per Requirement 3
    # -----------------------------------------------------------------------
    app.include_router(health.router)
    app.include_router(progress.router)
    app.include_router(jobs.router)
    app.include_router(answers.router)
    app.include_router(analytics.router)
    app.include_router(run.router)

    # -----------------------------------------------------------------------
    # Static Files and SPA Fallback per Requirement 8
    # -----------------------------------------------------------------------
    resolved_static_dir: Path | None = static_dir
    if resolved_static_dir is None:
        root = _find_project_root()
        frontend_dist = root / "frontend" / "dist"
        api_static = Path(__file__).parent / "static"
        if frontend_dist.is_dir():
            resolved_static_dir = frontend_dist
        elif api_static.is_dir():
            resolved_static_dir = api_static

    if resolved_static_dir and resolved_static_dir.is_dir():
        logger.info("Serving frontend static assets from %s", resolved_static_dir)
    else:
        logger.info("Static frontend directory not found. Running in API-only mode.")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, full_path: str) -> Any:
        # Don't hijack API routes
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="API route not found")

        if resolved_static_dir and resolved_static_dir.is_dir():
            file_target = resolved_static_dir / full_path
            if full_path and file_target.is_file():
                return FileResponse(file_target)
            index_path = resolved_static_dir / "index.html"
            if index_path.is_file():
                raw_html = index_path.read_text(encoding="utf-8")
                injected = inject_token_into_html(raw_html, app.state.token)
                return HTMLResponse(content=injected)

        return JSONResponse(
            status_code=404,
            content={"detail": "Not found", "code": "not_found"},
        )

    return app
