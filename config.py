import os
import sys


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"FATAL: Environment variable {name} is not set. Please configure it and restart.")
    return value


API_ID: int
try:
    API_ID = int(_require("API_ID"))
except ValueError:
    sys.exit("FATAL: API_ID must be an integer.")

API_HASH: str = _require("API_HASH")
PHONE: str = _require("PHONE")
CHANNEL_ID: str = _require("CHANNEL_ID")       # username or numeric ID
ADMIN_ID: int
try:
    ADMIN_ID = int(_require("ADMIN_ID"))
except ValueError:
    sys.exit("FATAL: ADMIN_ID must be an integer.")

SESSION_NAME: str = os.environ.get("SESSION_NAME", "tgradio")
CACHE_DIR: str = os.environ.get("CACHE_DIR", "cache")