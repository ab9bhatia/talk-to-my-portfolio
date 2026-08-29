"""Central redaction for logs, diagnostics, and support artifacts."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable


_SECRET_RE = re.compile(
    r"(?i)(api[_ -]?key|api[_ -]?secret|access[_ -]?token|totp|password|authorization|cookie)"
    r"\s*[:=]\s*[^\s,;]+"
)
_ACCOUNT_RE = re.compile(
    r"(?i)(account[_ -]?id|user[_ -]?id|client[_ -]?id)\s*[:=]\s*([^\s,;]+)"
)


def account_alias(account_id: str) -> str:
    return f"acct-{hashlib.sha256(account_id.encode()).hexdigest()[:10]}"


def redact_text(value: object, *, account_ids: Iterable[str] = (), limit: int = 1000) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _ACCOUNT_RE.sub(
        lambda match: f"{match.group(1)}={account_alias(match.group(2))}", text
    )
    for account_id in sorted({item for item in account_ids if item}, key=len, reverse=True):
        text = text.replace(account_id, account_alias(account_id))
    return text[:limit]
