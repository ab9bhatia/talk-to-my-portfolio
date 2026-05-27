"""Optional HTTP Basic Auth — protects the app when exposed beyond localhost."""

from __future__ import annotations

import base64
import os
import secrets
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Zerodha OAuth redirects cannot send Authorization headers.
_PUBLIC_PATH_PREFIXES = (
    "/auth/zerodha",
    "/zerodha/auth/",
)
_PUBLIC_EXACT = frozenset({"/health"})


def http_auth_username() -> str:
    return (os.getenv("PORTFOLIO_HTTP_USER") or os.getenv("PORTFOLIO_AUTH_USER") or "").strip()


def http_auth_password() -> str:
    return (os.getenv("PORTFOLIO_HTTP_PASSWORD") or os.getenv("PORTFOLIO_AUTH_PASSWORD") or "").strip()


def http_auth_enabled() -> bool:
    """Auth is on only when both user and password are set in .env."""
    return bool(http_auth_username() and http_auth_password())


def _path_is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Talk to My Portfolio", charset="UTF-8"'},
        content="Authentication required. Set PORTFOLIO_HTTP_USER and PORTFOLIO_HTTP_PASSWORD in .env.",
    )


def _credentials_match(authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    user, password = decoded.split(":", 1)
    expected_user = http_auth_username()
    expected_password = http_auth_password()
    user_ok = secrets.compare_digest(user, expected_user)
    pass_ok = secrets.compare_digest(password, expected_password)
    return user_ok and pass_ok


class HttpBasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic Auth on all routes except OAuth callbacks and /health."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not http_auth_enabled() or _path_is_public(request.url.path):
            return await call_next(request)
        if _credentials_match(request.headers.get("Authorization")):
            return await call_next(request)
        return _unauthorized()


def add_http_basic_auth(app: ASGIApp) -> ASGIApp:
    """Wrap app with auth middleware when credentials are configured."""
    if not http_auth_enabled():
        return app
    return HttpBasicAuthMiddleware(app)
