"""Resource and user-writable paths that work from source and from a PyInstaller bundle."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from app import APP_NAME


def resource_root() -> Path:
    """Folder holding bundled resources (sample_data/, powerbi/).

    PyInstaller unpacks data files under sys._MEIPASS; from source it is the project root.
    """
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def sample_data_path() -> Path:
    return resource_path("sample_data", "retail_sales_demo.csv")


def user_data_dir() -> Path:
    """Per-user writable folder for logs (never the install folder)."""
    base = os.environ.get("LOCALAPPDATA")
    folder = Path(base) / APP_NAME if base else Path(tempfile.gettempdir()) / APP_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path(tempfile.gettempdir())
    return folder


def icon_path() -> Path:
    """The application icon (assets/icon.png), bundled next to the other resources."""
    return resource_path("assets", "icon.png")
