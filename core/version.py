from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

try:
    APP_VERSION = _VERSION_FILE.read_text().strip()
except FileNotFoundError:
    APP_VERSION = "0.0.0"
