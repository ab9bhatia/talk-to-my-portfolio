"""Read and update local .env without dropping unrelated keys."""

from __future__ import annotations

import os
import re
from pathlib import Path

from shared.config import BASE_DIR

_ENV_PATH = BASE_DIR / ".env"
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def env_file_path() -> Path:
    return _ENV_PATH


def upsert_env_vars(updates: dict[str, str], *, path: Path | None = None) -> Path:
    """Insert or replace keys in .env; preserve other lines and comments."""
    target = path or _ENV_PATH
    lines: list[str] = []
    if target.is_file():
        lines = target.read_text(encoding="utf-8").splitlines()

    remaining = {k: v for k, v in updates.items() if v is not None}
    out: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        match = _KEY_RE.match(line)
        if not match:
            out.append(line)
            continue
        key = match.group(1)
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
            seen.add(key)
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- Added via portfolio setup ---")
        for key in sorted(remaining):
            out.append(f"{key}={remaining[key]}")

    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(out).rstrip() + "\n"
    target.write_text(text, encoding="utf-8")
    for key, value in updates.items():
        if value is not None:
            os.environ[key] = value
    return target


def env_var_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def read_env_value(name: str, *, path: Path | None = None) -> str:
    """Read a single key from .env file (falls back to os.environ)."""
    val = os.getenv(name, "").strip()
    if val:
        return val.strip('"').strip("'")
    target = path or _ENV_PATH
    if not target.is_file():
        return ""
    for line in target.read_text(encoding="utf-8").splitlines():
        match = _KEY_RE.match(line.strip())
        if match and match.group(1) == name:
            return match.group(2).strip().strip('"').strip("'")
    return ""
