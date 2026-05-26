"""Investor profile and portfolio-agent constraints (v1 — personal use)."""

from __future__ import annotations

import os

# Position limits (override via env if needed)
MAX_PCT_PER_STOCK = float(os.getenv("PORTFOLIO_MAX_PCT_PER_STOCK", "15"))
MAX_PCT_PER_SECTOR = float(os.getenv("PORTFOLIO_MAX_PCT_PER_SECTOR", "35"))

# Fundamentals filters
MAX_DEBT_TO_EQUITY = float(os.getenv("PORTFOLIO_MAX_DEBT_TO_EQUITY", "1.5"))

INVESTOR_PROFILE = {
    "horizon": "3+ years",
    "risk": "aggressive",
    "goal": "growth",
    "target_xirr_pct": 15.0,
    "max_drawdown_tolerance_pct": 20.0,
    "notes": (
        "Comfortable adding through corrections (e.g. post Oct 2024 peak). "
        "Values time correction + accumulation when thesis intact."
    ),
}

# Growth themes to prefer vs traditional (sector/symbol hints for the agent)
# Multi-word phrases only — never match ticker substrings (e.g. GRINFRA ≠ data centers).
PREFERRED_GROWTH_THEMES: list[dict[str, str]] = [
    {
        "theme": "Data centers & cloud infra",
        "keywords": "data center, colocation, hyperscale, cloud computing, server farm",
    },
    {
        "theme": "AI & semiconductors",
        "keywords": "artificial intelligence, semiconductor, gpu, machine learning platform",
    },
    {"theme": "Robotics & automation", "keywords": "robotics, industrial automation, warehouse automation"},
    {"theme": "Quantum computing", "keywords": "quantum computing"},
    {"theme": "Physical AI / edge", "keywords": "edge ai, autonomous vehicle, computer vision, iot platform"},
    {"theme": "Drones & defence tech", "keywords": "unmanned aerial, defence electronics, aerospace defence"},
]

TRADITIONAL_THEMES_TO_DEWEIGHT = [
    "legacy banking overweight",
    "commodity-only cyclicals without growth angle",
    "pure value traps with weak earnings momentum",
]

AVOID_FLAGS = [
    "very high debt to equity (see max threshold)",
    "governance concerns (pledging, fraud, auditor issues — flag if known)",
    "structurally declining sector without catalyst",
]
