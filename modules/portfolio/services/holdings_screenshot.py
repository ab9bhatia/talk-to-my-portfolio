"""Parse Indian broker portfolio screenshots into holding rows (INR)."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

_PARSE_PROMPT = """You are parsing a mobile or web portfolio holdings screenshot from an Indian broker
(Zerodha Kite, Groww, etc.). Extract every visible equity/MF holding.

Return JSON only:
{
  "rows": [
    {
      "symbol": "RELIANCE (NSE ticker, uppercase, no suffix)",
      "exchange": "NSE or BSE",
      "quantity": number,
      "avg_price": number in INR (average buy price if shown, else estimate from LTP),
      "last_price": number in INR (LTP / current price)
    }
  ]
}

Rules:
- symbol must be the tradable symbol only (e.g. RELIANCE not Reliance Industries).
- Skip cash balance-only lines without holdings.
- Use INR amounts as shown; do not convert currency.
- Do not invent rows not visible in the image."""


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


def vision_available() -> bool:
    return _api_key() is not None


def parse_holdings_screenshot(image_bytes: bytes, *, media_type: str = "image/png") -> dict[str, Any]:
    """Parse screenshot → rows for custom portfolio import (INR)."""
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "Screenshot import needs OPENAI_API_KEY (or PORTFOLIO_OPENAI_API_KEY) for vision parsing."
        )
    if not image_bytes:
        raise ValueError("Empty image")

    media_type = media_type if media_type.startswith("image/") else "image/png"
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
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Vision API error: {detail[:500]}") from exc

    text = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    parsed = json.loads(text)
    rows: list[dict[str, Any]] = []
    for raw in parsed.get("rows") or []:
        symbol = (raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        qty = float(raw.get("quantity") or 0)
        if qty <= 0:
            continue
        ltp = float(raw.get("last_price") or raw.get("avg_price") or 0)
        avg = float(raw.get("avg_price") or ltp or 0)
        rows.append({
            "symbol": symbol,
            "exchange": (raw.get("exchange") or "NSE").upper(),
            "quantity": qty,
            "avg_price": avg,
            "last_price": ltp or avg,
        })
    if not rows:
        raise ValueError("No holdings detected — try a clearer screenshot of your holdings list.")
    return {"rows": rows, "notes": "Screenshot import", "parsed_count": len(rows)}
