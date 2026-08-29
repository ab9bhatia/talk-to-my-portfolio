"""Deterministic portfolio advisory engine (Advisor V2), exposed lazily."""

from __future__ import annotations

from typing import Any


__all__ = ["advisory_for_llm", "build_advisory_payload", "build_live_advisory"]


def __getattr__(name: str) -> Any:
    if name == "build_advisory_payload":
        from modules.portfolio.services.advisory.service import build_advisory_payload

        return build_advisory_payload
    if name in {"advisory_for_llm", "build_live_advisory"}:
        from modules.portfolio.services.advisory.runtime import advisory_for_llm, build_live_advisory

        return {"advisory_for_llm": advisory_for_llm, "build_live_advisory": build_live_advisory}[name]
    raise AttributeError(name)
