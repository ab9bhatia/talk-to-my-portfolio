"""Deterministic portfolio advisory engine (Advisor V2)."""

from modules.portfolio.services.advisory.service import build_advisory_payload
from modules.portfolio.services.advisory.runtime import advisory_for_llm, build_live_advisory

__all__ = ["advisory_for_llm", "build_advisory_payload", "build_live_advisory"]
