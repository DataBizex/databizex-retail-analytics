"""Remember import options and column mapping per file layout.

A layout is the exact ordered list of a file's original column names. Entries are
kept in layout_memory.json in the user data folder (next to databizex.log). No Qt
here, so the memory can be tested on its own. A missing, unreadable or corrupt
file never raises: find returns None and remember starts the file afresh.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.data.column_mapping import EXPECTED_COLUMNS
from app.data.formats import DATE_FORMATS, DECIMALS, DELIMITERS, ENCODINGS, THOUSANDS, ImportOptions
from app.paths import user_data_dir

logger = logging.getLogger(__name__)

FILE_NAME = "layout_memory.json"
MAX_ENTRIES = 50
REMEMBERED_NOTE = "Settings remembered from a previous session."
_OPTION_CHOICES = {"delimiter": DELIMITERS, "encoding": ENCODINGS, "decimal": DECIMALS,
                   "thousands": THOUSANDS, "date_format": DATE_FORMATS}


def memory_path() -> Path:
    return user_data_dir() / FILE_NAME


def _key(columns) -> list[str]:
    return [str(c) for c in columns]


def _is_entry(entry: object) -> bool:
    return (isinstance(entry, dict)
            and isinstance(entry.get("columns"), list)
            and isinstance(entry.get("import_options"), (dict, type(None)))
            and isinstance(entry.get("column_mapping"), (dict, type(None))))


def _read_entries() -> list[dict]:
    """Valid entries, oldest first; [] when the file is missing, unreadable or corrupt."""
    path = memory_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable layout memory %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        logger.warning("Ignoring layout memory %s: not a list of entries", path)
        return []
    return [entry for entry in data if _is_entry(entry)]


def find(columns) -> dict | None:
    """The remembered entry for this exact column layout, or None."""
    key = _key(columns)
    for entry in _read_entries():
        if entry["columns"] == key:
            return entry
    return None


def remember(columns, import_options: dict | None, column_mapping: dict | None) -> None:
    """Insert or replace the entry for this layout and write the file (at most MAX_ENTRIES)."""
    key = _key(columns)
    entries = [entry for entry in _read_entries() if entry["columns"] != key]
    entries.append({
        "columns": key,
        "import_options": import_options,
        "column_mapping": column_mapping,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    entries = entries[-MAX_ENTRIES:]  # appended in save order, so the oldest are first
    path = memory_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # a convenience only: never stop a load over it
        logger.warning("Could not save layout memory %s: %s", path, exc)


def options_to_dict(options: ImportOptions) -> dict:
    return asdict(options)


def remembered_options(entry: dict | None, detected: ImportOptions) -> ImportOptions | None:
    """The entry's import options; a stored value that is not a valid choice falls back to detected."""
    stored = entry.get("import_options") if entry else None
    if not isinstance(stored, dict):
        return None
    values = {}
    for name, choices in _OPTION_CHOICES.items():
        value = stored.get(name)
        values[name] = value if isinstance(value, str) and value in choices else getattr(detected, name)
    if detected.delimiter is None:  # Excel: delimiter and encoding do not apply
        values["delimiter"] = values["encoding"] = None
    return ImportOptions(**values)


def remembered_mapping(entry: dict | None, columns) -> dict[str, str | None] | None:
    """The entry's column mapping, limited to fields whose source column exists in columns."""
    stored = entry.get("column_mapping") if entry else None
    if not isinstance(stored, dict):
        return None
    names = set(_key(columns))
    mapping = {target: stored[target] for target in EXPECTED_COLUMNS
               if target in stored and (stored[target] is None or stored[target] in names)}
    return mapping or None
