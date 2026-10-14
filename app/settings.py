"""Small user preferences (e.g. the theme) in settings.json in the user data folder.

Same defensive pattern as layout_memory and recent_files: a missing, unreadable
or corrupt file never raises (load returns {}), and writes go through a temp file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.paths import user_data_dir

logger = logging.getLogger(__name__)

FILE_NAME = "settings.json"
THEME_KEY = "theme"


def settings_path() -> Path:
    return user_data_dir() / FILE_NAME


def load() -> dict:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable settings %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring settings %s: not a JSON object", path)
        return {}
    return data


def get(key: str, default=None):
    return load().get(key, default)


def set_value(key: str, value) -> None:
    """Store one setting, keeping the others (a corrupt file is replaced)."""
    data = load()
    data[key] = value
    path = settings_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # a preference only: never stop the app over it
        logger.warning("Could not save settings %s: %s", path, exc)
