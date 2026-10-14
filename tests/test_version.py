"""The version lives in app/__init__.py only; everything else reads it from there."""

import os
import re

from app import __version__
from app.paths import resource_path


def test_version_is_semantic():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_changelog_top_release_matches_the_version():
    text = resource_path("CHANGELOG.md").read_text(encoding="utf-8")
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", text, flags=re.MULTILINE)
    assert releases and releases[0] == __version__
    assert text.index("## [Unreleased]") < text.index(f"## [{__version__}]")


def test_window_shows_the_version():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    win = main_window.MainWindow()
    try:
        assert win.version_label.text() == f"v{__version__}"
        assert f"v{__version__}" in main_window.ABOUT_TEXT
    finally:
        win.close()
        app.processEvents()
