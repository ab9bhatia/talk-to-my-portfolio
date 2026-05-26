"""Parse Sarwa mobile Trade summary screenshots into import rows."""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_PARSE_PROMPT = """You are parsing a Sarwa (UAE broker) mobile app "Trade" portfolio screenshot.
Extract every holding line (stocks, ETFs, crypto). Ignore account balance headers unless useful for notes.

Return JSON only:
{
  "account_balance_usd": number or null,
  "cash_usd": number or null,
  "rows": [
    {
      "symbol": "TICKER uppercase (e.g. AAPL, BTC, DTCR — use ticker not full name)",
      "name": "display name or null",
      "quantity": number,
      "last_price_usd": number (price shown next to quantity, LTP),
      "total_value_usd": number or null,
      "return_pct": number (total return % as shown, negative if red/loss),
      "asset_class": "equity" or "crypto"
    }
  ]
}

Rules:
- symbol must be tradeable ticker; for "Data Centers - DTCR" use DTCR.
- quantity is share count (BTC can be fractional).
- If only return % and value are visible, still set last_price_usd = total_value_usd / quantity.
- Do not invent holdings not visible in the image."""


def _vision_model() -> str:
    return (os.getenv("SARWA_VISION_MODEL") or os.getenv("PORTFOLIO_LLM_MODEL") or "gpt-4o-mini").strip()


def _api_key() -> str | None:
    key = (
        os.getenv("PORTFOLIO_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("API_KEY")
        or ""
    ).strip()
    return key or None


def _rows_from_return_pct(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive avg_price_usd when screenshot shows return % but not avg cost."""
    out: list[dict[str, Any]] = []
    for raw in rows:
        symbol = (raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        qty = float(raw.get("quantity") or 0)
        if qty <= 0:
            continue
        ltp = float(raw.get("last_price_usd") or 0)
        total = raw.get("total_value_usd")
        if total is not None:
            total = float(total)
        elif ltp > 0:
            total = qty * ltp
        else:
            continue

        avg = raw.get("avg_price_usd")
        if avg is None and raw.get("return_pct") is not None:
            ret = float(raw["return_pct"])
            invested = total / (1 + ret / 100.0) if abs(1 + ret / 100.0) > 1e-9 else total
            avg = invested / qty
        elif avg is None:
            avg = ltp

        out.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "last_price_usd": ltp or (total / qty),
                "avg_price_usd": float(avg),
                "exchange": raw.get("exchange") or "US",
                "asset_class": raw.get("asset_class") or "equity",
            }
        )
    return out


def _parse_via_openai(image_bytes: bytes, media_type: str) -> dict[str, Any]:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "Screenshot import needs OPENAI_API_KEY (or PORTFOLIO_OPENAI_API_KEY) for vision parsing."
        )

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    body = json.dumps(
        {
            "model": _vision_model(),
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PARSE_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64}"},
                        },
                    ],
                }
            ],
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Vision API error: {detail[:500]}") from exc

    text = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    return json.loads(text)


def parse_sarwa_screenshot(image_bytes: bytes, *, media_type: str = "image/png") -> dict[str, Any]:
    """
    Parse screenshot bytes → {rows, notes, account_balance_usd, cash_usd}.
    """
    if not image_bytes:
        raise ValueError("Empty image")

    media_type = media_type if media_type.startswith("image/") else "image/png"
    parsed = _parse_via_openai(image_bytes, media_type)
    rows = _rows_from_return_pct(parsed.get("rows") or [])
    if not rows:
        raise ValueError("No holdings detected in screenshot — try a clearer Trade summary image.")

    notes_parts = ["Sarwa screenshot import"]
    if parsed.get("account_balance_usd"):
        notes_parts.append(f"balance ${parsed['account_balance_usd']:,.2f}")
    if parsed.get("cash_usd"):
        notes_parts.append(f"cash ${parsed['cash_usd']:,.2f}")

    return {
        "rows": rows,
        "notes": "; ".join(notes_parts),
        "parsed_count": len(rows),
    }
