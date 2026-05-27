"""Safe upload helpers."""

from __future__ import annotations

import os

from fastapi import HTTPException, UploadFile

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def max_upload_bytes() -> int:
    raw = os.getenv("PORTFOLIO_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES


async def read_upload_bounded(file: UploadFile, *, max_bytes: int | None = None) -> bytes:
    """Read upload body with a hard size cap (DoS protection)."""
    limit = max_bytes if max_bytes is not None else max_upload_bytes()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {limit // (1024 * 1024)} MB)",
            )
        chunks.append(chunk)
    return b"".join(chunks)
