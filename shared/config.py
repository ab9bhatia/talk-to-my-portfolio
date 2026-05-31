"""Global application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Repository root (parent of shared/).
BASE_DIR = Path(__file__).resolve().parent.parent

APP_PORT = int(os.getenv("APP_PORT", "9000"))
APP_HOST = os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"

_root = os.getenv("APP_ROOT_PATH", "/talktomyportfolio").strip().rstrip("/")
APP_ROOT_PATH = f"/{_root.lstrip('/')}" if _root else ""

APP_BASE_URL = os.getenv(
    "APP_BASE_URL",
    os.getenv("HUB_BASE_URL", f"http://{APP_HOST}:{APP_PORT}{APP_ROOT_PATH}"),
).rstrip("/")

APP_NAME = os.getenv("APP_NAME", "TalkToMyPortfolio")
APP_TAGLINE = os.getenv(
    "APP_TAGLINE",
    "Consolidate brokers · talk to your portfolio",
)

# Optional links to sibling apps (separate repos / ports).
EXPENSES_APP_URL = os.getenv("EXPENSES_APP_URL", "").rstrip("/")
LEARNINGS_APP_URL = os.getenv("LEARNINGS_APP_URL", "").rstrip("/")
