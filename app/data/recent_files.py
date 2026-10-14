"""The most recently opened data files, for File > Open Recent.

Kept in recent_files.json in the user data folder (next to layout_memory.json),
most recent first, without duplicates. No Qt here. A missing, unreadable or
corrupt file never raises: get_paths returns [] and the next write starts afresh.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.paths import user_data_dir

logger = logging.getLogger(__name__)

FILE_NAME = "recent_files.json"
MAX_ENTRIES = 10


def recent_path() -> Path:
    return user_data_dir() / FILE_NAME


def _same(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)  # Windows paths ignore case


def get_paths() -> list[Path]:
    """Stored paths, most recent first; [] when the file is missing, unreadable or corrupt."""
    path = recent_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable recent files list %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        logger.warning("Ignoring recent files list %s: not a list of paths", path)
        return []
    return [Path(item) for item in data if isinstance(item, str) and item][:MAX_ENTRIES]


def _write(paths: list[str]) -> None:
    path = recent_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # a convenience only: never stop a load over it
        logger.warning("Could not save recent files list %s: %s", path, exc)


def add_path(path: str | Path) -> None:
    """Put path first, removing an earlier entry for the same file; keep MAX_ENTRIES."""
    new = str(Path(path).resolve())
    others = [str(p) for p in get_paths() if not _same(str(p), new)]
    _write([new, *others][:MAX_ENTRIES])


def remove_path(path: str | Path) -> None:
    gone = str(Path(path).resolve())
    _write([str(p) for p in get_paths() if not _same(str(p), gone)])


def clear() -> None:
    _write([])
