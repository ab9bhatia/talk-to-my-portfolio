"""Global application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Repository root (parent of shared/).
BASE_DIR = Path(__file__).resolve().parent.parent

APP_NAME = os.getenv("APP_NAME", "Talk to My Portfolio")
APP_TAGLINE = os.getenv(
    "APP_TAGLINE",
    "Consolidate brokers · talk to your portfolio",
)

# Optional links to sibling apps (separate repos / ports).
EXPENSES_APP_URL = os.getenv("EXPENSES_APP_URL", "").rstrip("/")
LEARNINGS_APP_URL = os.getenv("LEARNINGS_APP_URL", "").rstrip("/")
