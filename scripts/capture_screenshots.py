"""Capture README screenshots from the real application window.

Loads the bundled demo data, removes duplicates, runs the KPI queries and
writes a Power BI package to a temporary folder, then saves one PNG per tab
into screenshots/.

Run from the project root:
    .venv\\Scripts\\python scripts\\capture_screenshots.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.exports.exporter import generate_powerbi_package  # noqa: E402
from app.main import create_application  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "screenshots"
SHOTS = ["01_import_preview.png", "02_data_quality.png", "03_sql_kpis.png", "04_export_reporting.png"]


def main() -> int:
    app = create_application(sys.argv, theme_mode="light")  # README screenshots use the light theme
    window = MainWindow()
    window.resize(1360, 860)
    window.show()
    app.processEvents()

    if not window.load_sample():
        print("Sample data could not be loaded")
        return 1
    OUT.mkdir(exist_ok=True)

    # Tab 1 before cleaning, so the preview shows the original import.
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    window.grab().save(str(OUT / SHOTS[0]))

    window.remove_duplicates()
    window.run_kpis()
    with tempfile.TemporaryDirectory() as tmp:
        written = generate_powerbi_package(window.working_df, Path(tmp), window.source_path.name,
                                           window.data_state_text())
        window._log_export("Power BI package written to <chosen folder>: " + ", ".join(p.name for p in written))

    for index, name in enumerate(SHOTS[1:], start=1):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        window.grab().save(str(OUT / name))

    for name in SHOTS:
        print(OUT / name)
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
