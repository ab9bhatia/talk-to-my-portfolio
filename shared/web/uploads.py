"""Safe upload helpers."""

from __future__ import annotations

import os
from pathlib import PurePath, PureWindowsPath

from fastapi import HTTPException, UploadFile

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")


def validate_upload_name(filename: str, *, allowed_extensions: set[str]) -> str:
    if (
        not filename
        or "\x00" in filename
        or PurePath(filename).name != filename
        or PureWindowsPath(filename).name != filename
    ):
        raise HTTPException(status_code=400, detail="Unsafe upload filename.")
    extension = os.path.splitext(filename.lower())[1]
    if extension not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Unsupported upload file type.")
    return extension


def max_upload_bytes() -> int:
    raw = os.getenv("PORTFOLIO_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES


async def read_upload_bounded(
    file: UploadFile, *, max_bytes: int | None = None,
    allowed_extensions: set[str] | None = None, allowed_content_types: set[str] | None = None,
    require_image_signature: bool = False,
) -> bytes:
    """Read upload body with a hard size cap (DoS protection)."""
    if allowed_extensions is not None:
        validate_upload_name(file.filename or "", allowed_extensions=allowed_extensions)
    if allowed_content_types is not None and (file.content_type or "").lower() not in allowed_content_types:
        raise HTTPException(status_code=415, detail="Upload content type does not match the endpoint.")
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
    content = b"".join(chunks)
    image_valid = content.startswith(IMAGE_SIGNATURES[:2]) or (
        content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP"
    )
    if require_image_signature and not image_valid:
        raise HTTPException(status_code=415, detail="Invalid image signature.")
    return content
