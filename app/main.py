"""DataBizex entry point.

    python -m app.main                      start the GUI
    python -m app.main --smoke-test OUT     start, load the sample, run checks + KPIs,
                                            write a JSON report to OUT and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_NAME, __version__
from app.paths import user_data_dir

logger = logging.getLogger("databizex")


def setup_logging() -> Path:
    log_file = user_data_dir() / "databizex.log"
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        pass  # logging to file is optional; the app still works without it
    if sys.stderr is not None:  # windowed exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    return log_file


def install_exception_hook() -> None:
    """Show unexpected errors to the user instead of silently closing."""
    def hook(exc_type, exc, tb):
        logger.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "Unexpected error",
                                 "".join(traceback.format_exception_only(exc_type, exc)).strip()
                                 + f"\n\nDetails were written to {user_data_dir() / 'databizex.log'}")
    sys.excepthook = hook


def create_application(argv: list[str]) -> QApplication:
    """QApplication with a fixed light colour scheme so the UI looks the same
    whether Windows runs in light or dark mode."""
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    return app


def run_smoke_test(window, output: Path) -> None:
    """Exercise the main path in the real window and write the results to JSON."""
    report: dict = {"version": __version__, "window_title": window.windowTitle()}
    try:
        report["sample_loaded"] = window.load_sample()
        report["rows_original"] = len(window.original_df)
        quality = window.quality_table.model_._df
        report["duplicates_found"] = int(quality.loc[quality["check"] == "Exact duplicate rows", "count"].iloc[0])
        report["missing_cells"] = int(quality.loc[quality["check"] == "Missing values (all columns)", "count"].iloc[0])
        window.remove_duplicates()
        report["rows_working"] = len(window.working_df)
        report["kpis_ok"] = window.run_kpis()
        report["kpis"] = {k: (None if v != v else v) for k, v in window.kpi_headline.items()}
        report["kpi_basis"] = window.kpi_basis
        report["preview_rows_in_gui"] = window.preview_table.model_.rowCount()
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - recorded in the report
        report["ok"] = False
        report["error"] = repr(exc)
        logger.exception("Smoke test failed")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    QApplication.instance().exit(0 if report["ok"] else 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--smoke-test", metavar="OUTPUT_JSON", type=Path,
                        help="load the sample data, run checks and KPIs, write a JSON report and exit")
    args = parser.parse_args(argv)

    log_file = setup_logging()
    logger.info("Starting %s v%s (log: %s)", APP_NAME, __version__, log_file)

    app = create_application(sys.argv)
    install_exception_hook()

    from app.ui.main_window import MainWindow  # imported after QApplication exists

    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(500, lambda: run_smoke_test(window, args.smoke_test))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
