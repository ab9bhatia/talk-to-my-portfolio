"""URL helpers for optional APP_ROOT_PATH (e.g. /talktomyportfolio)."""

from __future__ import annotations

from shared.config import APP_ROOT_PATH


def app_path(path: str = "/") -> str:
    """Browser path with APP_ROOT_PATH prefix (no scheme/host)."""
    if not path or path == "/":
        return APP_ROOT_PATH or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{APP_ROOT_PATH}{path}" if APP_ROOT_PATH else path
