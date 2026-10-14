"""DataBizex entry point.

    python -m app.main                      start the GUI
    python -m app.main --smoke-test OUT     start, load the demo data, run checks + KPIs,
                                            check the messy sample files, write a JSON
                                            report to OUT and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_NAME, __version__, settings
from app.exports.exporter import export_excel, generate_powerbi_package
from app.paths import icon_path, user_data_dir
from app.ui import theme

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


def saved_theme_mode() -> str:
    """The theme chosen under View > Theme (settings.json); System when unset or unknown."""
    mode = settings.get(settings.THEME_KEY, theme.SYSTEM_MODE)
    return mode if mode in theme.MODES else theme.SYSTEM_MODE


def create_application(argv: list[str], theme_mode: str | None = None) -> QApplication:
    """QApplication with the app icon and the saved (or given) light / dark / system theme:
    Fusion style, the theme's style sheet and palette, Segoe UI."""
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = icon_path()
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    theme.apply_theme(app, theme_mode or saved_theme_mode())
    return app


def run_smoke_test(window, output: Path) -> None:
    """Exercise the main path in the real window and write the results to JSON."""
    report: dict = {"version": __version__, "window_title": window.windowTitle()}
    try:
        report["sample_loaded"] = window.load_sample()
        report["rows_original"] = len(window.original_df)
        report["rule_count"] = len(window.rules)
        quality = window.quality_table.model_._df
        report["duplicates_found"] = int(quality.loc[quality["check"] == "Exact duplicate rows", "count"].iloc[0])
        report["missing_cells"] = int(quality.loc[quality["check"] == "Missing values (all columns)", "count"].iloc[0])
        window.remove_duplicates()
        report["rows_working"] = len(window.working_df)
        report["kpis_ok"] = window.run_kpis()
        report["kpis"] = {k: (None if v != v else v) for k, v in window.kpi_headline.items()}
        report["kpi_basis"] = window.kpi_basis
        report["preview_rows_in_gui"] = window.preview_table.model_.rowCount()
        # File > Open sample dataset: the bundled messy tables are found and readable.
        from app.data.loader import load_file
        from app.paths import messy_sample_dir
        from app.ui.main_window import messy_samples

        window._rebuild_sample_menu()
        report["sample_menu_enabled"] = window.sample_menu.menuAction().isEnabled()
        report["sample_menu_entries"] = [action.text() for action in window.sample_menu.actions()]
        report["messy_sample_rows"] = {path.name: len(load_file(path).df)
                                       for _label, path in messy_samples(messy_sample_dir())}
        with tempfile.TemporaryDirectory() as tmp:
            written = generate_powerbi_package(window.working_df, Path(tmp), "smoke-test", window.kpi_basis)
            report["powerbi_files"] = sorted(p.name for p in written)
            report["xlsx_export_bytes"] = export_excel(window.working_df, Path(tmp) / "w.xlsx").stat().st_size
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
                        help="load the demo data, run checks and KPIs, write a JSON report and exit")
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
