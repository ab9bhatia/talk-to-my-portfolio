"""Optional HTTP Basic Auth — protects the app when exposed beyond localhost."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from shared.config import APP_ROOT_PATH

# Zerodha OAuth redirects cannot send Authorization headers.
_PUBLIC_PATH_PREFIXES = (
    "/auth/zerodha",
    "/zerodha/auth/",
)
_PUBLIC_EXACT = frozenset({"/health"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[tuple[str, str], list[float]] = {}


def _path_without_root(path: str) -> str:
    if APP_ROOT_PATH and path.startswith(APP_ROOT_PATH):
        rest = path[len(APP_ROOT_PATH) :]
        return rest if rest.startswith("/") else f"/{rest}" if rest else "/"
    return path


def http_auth_username() -> str:
    return (os.getenv("PORTFOLIO_HTTP_USER") or os.getenv("PORTFOLIO_AUTH_USER") or "").strip()


def http_auth_password() -> str:
    return (os.getenv("PORTFOLIO_HTTP_PASSWORD") or os.getenv("PORTFOLIO_AUTH_PASSWORD") or "").strip()


def http_auth_enabled() -> bool:
    """Auth is on only when both user and password are set in .env."""
    return bool(http_auth_username() and http_auth_password())


def access_auth_enabled() -> bool:
    return http_auth_enabled() or bool(bearer_token())


def bearer_token() -> str:
    return os.getenv("PORTFOLIO_BEARER_TOKEN", "").strip()


def csrf_token() -> str:
    material = f"{http_auth_username()}:{http_auth_password()}".encode()
    return hmac.new(material, b"talk-to-my-portfolio-csrf-v1", hashlib.sha256).hexdigest()


def _path_is_public(path: str) -> bool:
    path = _path_without_root(path)
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="TalkToMyPortfolio", charset="UTF-8"'},
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


def _authorization_kind(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer ") and bearer_token():
        if secrets.compare_digest(authorization[7:].strip(), bearer_token()):
            return "bearer"
    if _credentials_match(authorization):
        return "basic"
    return None


def _csrf_valid(request: Request, auth_kind: str | None) -> bool:
    if request.method in _SAFE_METHODS or auth_kind == "bearer":
        return True
    supplied = request.headers.get("X-Portfolio-CSRF", "")
    if supplied and secrets.compare_digest(supplied, csrf_token()):
        return True
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return False
    supplied_url = urlsplit(origin)
    expected_url = urlsplit(str(request.base_url))
    return (supplied_url.scheme, supplied_url.netloc) == (
        expected_url.scheme,
        expected_url.netloc,
    )


def _rate_category(path: str) -> tuple[str, int, int] | None:
    lowered = path.lower()
    if "auth" in lowered or "login" in lowered:
        return "login", 20, 60
    if "sync" in lowered:
        return "sync", 30, 60
    if "upload" in lowered or "import" in lowered or "screenshot" in lowered:
        return "upload", 20, 60
    if "agent" in lowered or "llm" in lowered:
        return "llm", 30, 60
    return None


def _rate_limited(request: Request) -> bool:
    policy = _rate_category(request.url.path)
    if policy is None:
        return False
    category, limit, window = policy
    client = request.client.host if request.client else "local"
    now = time.monotonic()
    key = (client, category)
    with _RATE_LOCK:
        recent = [stamp for stamp in _RATE_BUCKETS.get(key, []) if now - stamp < window]
        if len(recent) >= limit:
            _RATE_BUCKETS[key] = recent
            return True
        recent.append(now)
        _RATE_BUCKETS[key] = recent
    return False


def _secure(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'",
    )
    response.headers.setdefault("Cache-Control", "no-store")
    return response


class HttpBasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic Auth on all routes except OAuth callbacks and /health."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if _rate_limited(request):
            return _secure(Response(status_code=429, content="Too many requests; retry later."))
        if not access_auth_enabled() or _path_is_public(request.url.path):
            return _secure(await call_next(request))
        auth_kind = _authorization_kind(request.headers.get("Authorization"))
        if auth_kind is None:
            return _secure(_unauthorized())
        if not _csrf_valid(request, auth_kind):
            return _secure(Response(status_code=403, content="CSRF validation failed."))
        return _secure(await call_next(request))


def add_http_basic_auth(app: ASGIApp) -> ASGIApp:
    """Install dynamic auth/security middleware; env changes apply after restart or in tests."""
    add_middleware = getattr(app, "add_middleware", None)
    if add_middleware:
        add_middleware(HttpBasicAuthMiddleware)
        return app
    return HttpBasicAuthMiddleware(app)
