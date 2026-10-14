"""Capture README screenshots from the real application window.

Loads the bundled demo data, removes duplicates and runs the KPI queries, then
saves one PNG per tab into screenshots/. Then opens the messy sales sample and
captures the Fix dialog (Remove duplicates, with the score change in its
preview) and the Change log view after three fixes that each raise the score.

The window is rendered without appearing on screen, in the light theme, and
the status bar is cleared before every grab.

Run from the project root:
    .venv\\Scripts\\python scripts\\capture_screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from app.data import fixes  # noqa: E402
from app.main import create_application  # noqa: E402
from app.paths import messy_sample_dir  # noqa: E402
from app.ui import main_window  # noqa: E402
from app.ui.fix_dialog import FixDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "screenshots"
SHOTS = ["01_import_preview.png", "02_data_quality.png", "03_sql_kpis.png", "04_export_reporting.png"]
FIX_SHOT, LOG_SHOT = "05_fix_dialog.png", "06_change_log.png"
# Fixes for the Change log screenshot, applied in order; each one raises the quality score.
LOG_FIXES = [
    ("Exact duplicate rows", fixes.FixAction.FIX_DUPLICATES, None),
    ("Missing values: discount_pct", fixes.FixAction.FILL_CONSTANT, {"value": "0"}),
    ("discount_pct outside 0-100", fixes.FixAction.EXCLUDE_ROWS, None),
]


def save(app, window, widget, name: str) -> Path:
    """Clear the status bar, grab widget and write an optimized PNG."""
    window.show_status("", "hint")
    app.processEvents()
    path = OUT / name
    widget.grab().save(str(path))
    with Image.open(path) as image:
        image.convert("RGB").save(path, optimize=True)
    return path


def main() -> int:
    app = create_application(sys.argv, theme_mode="light")  # README screenshots use the light theme
    window = MainWindow()
    window.setAttribute(Qt.WA_DontShowOnScreen, True)  # rendered for the grabs, never shown
    window.resize(1360, 860)
    window.show()
    app.processEvents()

    if not window.load_sample():
        print("Sample data could not be loaded")
        return 1
    OUT.mkdir(exist_ok=True)
    written = []

    # Tab 1 before cleaning, so the preview shows the original import.
    window.tabs.setCurrentIndex(0)
    written.append(save(app, window, window, SHOTS[0]))

    window.remove_duplicates()
    window.run_kpis()
    for index, name in enumerate(SHOTS[1:], start=1):
        window.tabs.setCurrentIndex(index)
        written.append(save(app, window, window, name))

    # The messy sales sample: Cancel the import and mapping dialogs (loaded as read), then fix.
    main_window.ImportOptionsDialog.exec = lambda dialog: 0
    main_window.ColumnMappingDialog.exec = lambda dialog: 0
    if not window.load_path(messy_sample_dir() / "retail_messy_4_sales.xlsx"):
        print("Messy sample could not be loaded")
        return 1

    name, action, params = LOG_FIXES[0]
    rule = window._rules_by_check()[name]
    dialog = FixDialog(window.working_df, rule, window.rules, int(window.row_flags[name].sum()),
                       window.unavailable_columns, window)
    dialog.radios[action].click()
    dialog.update_preview()
    dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    dialog.resize(780, 680)
    dialog.show()
    written.append(save(app, window, dialog, FIX_SHOT))
    dialog.close()

    for name, action, params in LOG_FIXES:
        rule = window._rules_by_check()[name]
        new_df, entry = fixes.run_fix(window.working_df, rule, action, params, window.rules,
                                      window.unavailable_columns)
        window._apply_change(new_df, entry, "Fix applied.")
    window.tabs.setCurrentIndex(1)
    window.quality_views.setCurrentIndex(1)  # Change log, last entry selected
    written.append(save(app, window, window, LOG_SHOT))

    for path in written:
        print(path)
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
