"""Portfolio module path constants."""

import os
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("PORTFOLIO_DATA_DIR") or MODULE_DIR / "data").expanduser()
