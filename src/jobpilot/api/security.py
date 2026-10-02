"""Security configuration, token validation, and host filtering for JobPilot API."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

HEADER_TOKEN_NAME = "X-JobPilot-Token"
META_TAG_FORMAT = '<meta name="jobpilot-token" content="{token}">'


def generate_or_get_token(dev: bool = False) -> str:
    """Generate a high-entropy token or read JOBPILOT_DEV_TOKEN in dev mode."""
    if dev:
        env_token = os.environ.get("JOBPILOT_DEV_TOKEN")
        if env_token:
            return env_token
        token = secrets.token_urlsafe(32)
        print(f"🔑 [JobPilot Dev Mode] Generated API Token: {token}")
        return token
    return secrets.token_urlsafe(32)


def inject_token_into_html(html: str, token: str) -> str:
    """Inject <meta name="jobpilot-token" content="..."> into HTML for the SPA."""
    meta_tag = META_TAG_FORMAT.format(token=token)
    if "</head>" in html:
        return html.replace("</head>", f"  {meta_tag}\n</head>", 1)
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n  {meta_tag}", 1)
    return f"{meta_tag}\n{html}"


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Enforces X-JobPilot-Token header verification on non-GET mutating requests."""

    def __init__(self, app: Any, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        # Non-GET requests (except OPTIONS for CORS preflight and HEAD) require token
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            provided_token = request.headers.get(HEADER_TOKEN_NAME)
            if not provided_token or not secrets.compare_digest(provided_token, self.token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid or missing X-JobPilot-Token", "code": "forbidden"},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces production security headers: CSP, X-Content-Type-Options, Referrer-Policy."""

    PROD_CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
        "base-uri 'none'; form-action 'self'"
    )

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self.PROD_CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def configure_security(app: Any, token: str, dev: bool = False) -> None:
    """Configure security middleware: TrustedHost, TokenAuth, Dev CORS, and Prod Security Headers.

    In Starlette, middleware added later wraps around earlier middleware.
    Adding TrustedHostMiddleware last ensures it executes first on incoming requests.
    """
    # 1. TokenAuthMiddleware for CSRF/API protection
    app.add_middleware(TokenAuthMiddleware, token=token)

    # 2. CORS or Production Security Headers
    if dev:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", HEADER_TOKEN_NAME],
            allow_credentials=False,
        )
    else:
        app.add_middleware(SecurityHeadersMiddleware)

    # 3. TrustedHostMiddleware: allow only localhost and 127.0.0.1 (added last so it runs FIRST)
    # Note: Starlette's parse_host_header strips the port, so "localhost" matches "localhost:8765".
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
    )
