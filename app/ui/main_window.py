"""DataBizex main window: Import & Preview, Data Quality, SQL & KPIs, Export & Reporting."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import matplotlib

matplotlib.use("QtAgg")

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.analytics.kpis import KpiInputError, format_margin, validity_summary
from app.data.cleaner import change_summary, remove_exact_duplicates, run_quality_checks
from app.data.column_mapping import ColumnMappingError, apply_column_mapping, guess_mapping, needs_mapping
from app.data.formats import (
    ConversionResult,
    FormatDetection,
    ImportOptions,
    apply_import_options,
    detect_formats,
    import_summary,
)
from app.data.failed_rows import ALL_FAILED, failed_rule_choices, failed_rows
from app.data.loader import FILE_DIALOG_FILTER, DataLoadError, load_file, with_source_rows
from app.data.normalize import apply_normalization, find_text_variants
from app.data.profiler import dataset_overview, numeric_summary, profile_columns
from app.data.rules import (
    RULES_FILE_FILTER,
    QualityScore,
    RulesError,
    default_rules,
    default_rules_path,
    load_rules,
    quality_score,
    validate,
)
from app.database.duckdb_manager import QUERIES, DuckDBManager
from app.exports.exporter import (
    ExportError,
    export_csv,
    export_excel,
    export_tables_csv,
    generate_powerbi_package,
)
from app.paths import sample_data_path, user_data_dir
from app.ui.column_mapping_dialog import ColumnMappingDialog
from app.ui.import_options_dialog import ImportOptionsDialog
from app.ui.normalize_dialog import NormalizeDialog
from app.ui.data_table import DataTable

logger = logging.getLogger(__name__)

WINDOW_TITLE = "DataBizex — Retail Analytics & Data Quality"
PREVIEW_ROWS = 100
ABOUT_TEXT = (
    f"DataBizex v{__version__} — Built by Ali Soltanhosseini, data analyst, from everyday "
    "industry workflows: validating messy sales exports, computing business KPIs and "
    "preparing data for Power BI. DataBizex.com"
)
KPI_TABLE_KEYS = ["by_country", "by_category", "by_month", "top_products"]
FAILED_ROWS_SHOWN = 500
SCORE_TITLES = {
    "overall": "Quality score",
    "completeness": "Completeness",
    "validity": "Validity",
    "uniqueness": "Uniqueness",
    "consistency": "Consistency",
}
SCORE_TOOLTIP = (
    "Each dimension = % of rows that fail none of its rules "
    "(completeness: not-null; validity: type, range, allowed values, pattern; "
    "uniqueness: duplicates; consistency: cross-column). "
    "Overall = average of the available dimensions. Outliers are not scored."
)


def _money(value: float) -> str:
    return "n/a" if pd.isna(value) else f"€ {value:,.2f}"


class KpiCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        caption = QLabel(title)
        caption.setStyleSheet("color: #555;")
        self.value = QLabel("—")
        font = QFont()
        font.setPointSize(15)
        font.setBold(True)
        self.value.setFont(font)
        layout.addWidget(caption)
        layout.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class ChartCanvas(FigureCanvasQTAgg):
    def __init__(self) -> None:
        self.figure = Figure(figsize=(5, 3), tight_layout=True)
        super().__init__(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.clear("Run KPI queries to draw this chart")

    def clear(self, message: str) -> None:
        self.ax.clear()
        self.ax.set_axis_off()
        self.ax.text(0.5, 0.5, message, ha="center", va="center", color="#777")
        self.draw_idle()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1280, 820)

        # Data state. original_df is never modified after loading.
        self.source_path: Path | None = None
        self.original_df: pd.DataFrame | None = None
        self.working_df: pd.DataFrame | None = None
        # Source file line number of each original row; the working copy is indexed by these.
        self.source_rows: list[int] = []
        # Latest validation of the working copy, for the failed-rows view and export.
        self.quality_summary: pd.DataFrame | None = None
        self.row_flags: pd.DataFrame | None = None
        # Import options (delimiter, encoding, number and date formats) for the current file.
        self.import_options = ImportOptions()
        self.conversion: ConversionResult | None = None
        # Column mapping chosen for the current file (None = names used as they are).
        self.column_mapping: dict[str, str | None] | None = None
        self.unavailable_columns: list[str] = []
        self.mapping_notes: list[str] = []
        self.duplicates_removed = 0
        self.normalized_columns: list[str] = []  # working-copy columns with text variants merged
        self.kpi_results: dict[str, pd.DataFrame] = {}
        self.kpi_headline: dict[str, float] = {}
        self.kpi_basis = ""
        self.db = DuckDBManager()
        # Validation rule set: the shipped retail default until the user loads another.
        self.rules = []
        self.rules_source = "no rule set"
        try:
            self.rules = default_rules()
            self.rules_source = default_rules_path().name
        except RulesError:
            logger.exception("Default rule set could not be loaded")

        self._build_menu()
        self._build_ui()
        self._refresh_all()

    # ------------------------------------------------------------------ UI setup
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open data file...", self, shortcut="Ctrl+O", triggered=self.open_file)
        sample_action = QAction("Load &sample data", self, triggered=self.load_sample)
        exit_action = QAction("E&xit", self, shortcut="Ctrl+Q", triggered=self.close)
        file_menu.addActions([open_action, sample_action])
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction("&About DataBizex", self, triggered=self.show_about))

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        title = QLabel("DataBizex")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        subtitle = QLabel("Retail Analytics & Data Quality")
        subtitle.setStyleSheet("color: #555;")
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setStyleSheet("color: #777;")
        open_btn = QPushButton("Open file...")
        open_btn.clicked.connect(self.open_file)
        sample_btn = QPushButton("Load sample data")
        sample_btn.clicked.connect(self.load_sample)
        about_btn = QPushButton("About")
        about_btn.clicked.connect(self.show_about)
        header.addWidget(title)
        header.addWidget(subtitle)
        header.addWidget(self.version_label)
        header.addStretch(1)
        header.addWidget(open_btn)
        header.addWidget(sample_btn)
        header.addWidget(about_btn)
        root.addLayout(header)

        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("padding: 6px; background: #eef3f8; border-radius: 4px;")
        root.addWidget(self.state_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_import_tab(), "Import && Preview")
        self.tabs.addTab(self._build_quality_tab(), "Data Quality")
        self.tabs.addTab(self._build_kpi_tab(), "SQL && KPIs")
        self.tabs.addTab(self._build_export_tab(), "Export && Reporting")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate; shown only while work runs
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Ready. Open a file or load the sample data.")

    def _build_import_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.import_info = QLabel()
        self.import_info.setWordWrap(True)
        layout.addWidget(self.import_info)

        splitter = QSplitter(Qt.Vertical)
        preview_box = QGroupBox(f"Preview (first {PREVIEW_ROWS} rows)")
        QVBoxLayout(preview_box).addWidget(table := DataTable())
        self.preview_table = table
        splitter.addWidget(preview_box)

        lower = QSplitter(Qt.Horizontal)
        profile_box = QGroupBox("Column profile")
        QVBoxLayout(profile_box).addWidget(table := DataTable())
        self.profile_table = table
        stats_box = QGroupBox("Numeric statistics (columns with at least 80% numbers)")
        QVBoxLayout(stats_box).addWidget(table := DataTable(stretch_last=False))
        self.stats_table = table
        lower.addWidget(profile_box)
        lower.addWidget(stats_box)
        splitter.addWidget(lower)
        splitter.setSizes([420, 300])
        layout.addWidget(splitter, 1)
        return tab

    def _build_quality_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        self.dedupe_btn = QPushButton("Remove exact duplicates")
        self.dedupe_btn.clicked.connect(self.remove_duplicates)
        self.normalize_btn = QPushButton("Normalize text...")
        self.normalize_btn.clicked.connect(self.normalize_text)
        self.reset_btn = QPushButton("Reset working copy to original")
        self.reset_btn.clicked.connect(self.reset_working_copy)
        self.load_rules_btn = QPushButton("Load rule set...")
        self.load_rules_btn.clicked.connect(self.load_rule_set)
        self.rules_label = QLabel()
        self.rules_label.setStyleSheet("color: #555;")
        self._update_rules_label()
        buttons.addWidget(self.dedupe_btn)
        buttons.addWidget(self.normalize_btn)
        buttons.addWidget(self.reset_btn)
        buttons.addWidget(self.load_rules_btn)
        buttons.addWidget(self.rules_label)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        note = QLabel(
            "Checks only report problems. Rows with missing values are never deleted or filled. "
            "Removing duplicates and normalizing text change the in-memory working copy only — "
            "the source file is never modified."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")
        layout.addWidget(note)

        scores = QHBoxLayout()
        self.score_labels: dict[str, QLabel] = {}
        for key in SCORE_TITLES:
            label = QLabel()
            label.setStyleSheet("padding: 4px 10px; background: #eef3f8; border-radius: 4px;")
            label.setToolTip(SCORE_TOOLTIP)
            self.score_labels[key] = label
            scores.addWidget(label)
        scores.addStretch(1)
        layout.addLayout(scores)
        self._show_score(None)

        splitter = QSplitter(Qt.Horizontal)
        quality_box = QGroupBox("Quality summary (working data)")
        QVBoxLayout(quality_box).addWidget(table := DataTable())
        self.quality_table = table
        change_box = QGroupBox("Before / after: original import vs working data")
        QVBoxLayout(change_box).addWidget(table := DataTable())
        self.change_table = table
        splitter.addWidget(quality_box)
        splitter.addWidget(change_box)
        splitter.setSizes([700, 500])

        failed_box = QGroupBox("Failed rows (working data)")
        failed_layout = QVBoxLayout(failed_box)
        failed_top = QHBoxLayout()
        self.failed_combo = QComboBox()
        self.failed_combo.setMinimumWidth(320)
        self.failed_combo.currentIndexChanged.connect(self._show_failed_rows)
        self.failed_count_label = QLabel()
        self.failed_count_label.setStyleSheet("color: #555;")
        self.export_failed_btn = QPushButton("Export failed rows to CSV...")
        self.export_failed_btn.clicked.connect(self.export_failed_rows)
        failed_top.addWidget(QLabel("Rule:"))
        failed_top.addWidget(self.failed_combo)
        failed_top.addWidget(self.failed_count_label)
        failed_top.addStretch(1)
        failed_top.addWidget(self.export_failed_btn)
        failed_layout.addLayout(failed_top)
        self.failed_table = DataTable()
        failed_layout.addWidget(self.failed_table)

        vertical = QSplitter(Qt.Vertical)
        vertical.addWidget(splitter)
        vertical.addWidget(failed_box)
        vertical.setSizes([420, 300])
        layout.addWidget(vertical, 1)
        return tab

    def _build_kpi_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        self.run_kpi_btn = QPushButton("Run KPI queries (DuckDB)")
        self.run_kpi_btn.clicked.connect(self.run_kpis)
        self.kpi_basis_label = QLabel()
        self.kpi_basis_label.setWordWrap(True)
        top.addWidget(self.run_kpi_btn)
        top.addWidget(self.kpi_basis_label, 1)
        layout.addLayout(top)

        cards = QGridLayout()
        self.cards = {
            "net_revenue": KpiCard("Net revenue"),
            "units_sold": KpiCard("Units sold"),
            "gross_profit": KpiCard("Estimated gross profit"),
            "gross_margin": KpiCard("Gross margin"),
            "orders": KpiCard("Order lines used"),
        }
        for i, card in enumerate(self.cards.values()):
            cards.addWidget(card, 0, i)
        layout.addLayout(cards)

        self.exclusion_label = QLabel()
        self.exclusion_label.setWordWrap(True)
        self.exclusion_label.setStyleSheet("color: #8a4b00;")
        layout.addWidget(self.exclusion_label)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.query_combo = QComboBox()
        for key in KPI_TABLE_KEYS:
            self.query_combo.addItem(QUERIES[key].title, key)
        self.query_combo.currentIndexChanged.connect(self._show_selected_query)
        left_layout.addWidget(self.query_combo)
        self.result_table = DataTable()
        left_layout.addWidget(self.result_table, 2)
        sql_label = QLabel("SQL (standard, read-only; runs unchanged on MySQL/PostgreSQL):")
        left_layout.addWidget(sql_label)
        self.sql_view = QPlainTextEdit()
        self.sql_view.setReadOnly(True)
        self.sql_view.setFont(QFont("Consolas", 9))
        left_layout.addWidget(self.sql_view, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.country_chart = ChartCanvas()
        self.month_chart = ChartCanvas()
        right_layout.addWidget(self.country_chart)
        right_layout.addWidget(self.month_chart)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([560, 640])
        layout.addWidget(splitter, 1)
        self._show_selected_query()
        return tab

    def _build_export_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        actions = [
            ("Export working data to CSV...", self.export_working_csv,
             "All rows and columns of the current working copy, as CSV (UTF-8)."),
            ("Export working data to Excel...", self.export_working_excel,
             "The working copy as an .xlsx workbook (one sheet)."),
            ("Export quality report to CSV...", self.export_quality_report,
             "The quality summary table for the working data."),
            ("Export failed rows to CSV...", self.export_failed_rows,
             "Every working row that failed the rule chosen under Failed rows on the Data Quality tab "
             "(default: all rows failing an error rule), with source_row and failed_rules columns."),
            ("Export KPI tables to CSV...", self.export_kpi_tables,
             "Headline KPIs and every KPI result table, one CSV per table, into a folder you choose."),
            ("Generate Power BI package...", self.export_powerbi,
             "sales_clean.csv (rows valid for KPIs), data_dictionary.md, POWER_BI_GUIDE.md and "
             "package_info.txt in a folder you choose. No .pbix file is created."),
        ]
        grid = QGridLayout()
        self.export_buttons: list[QPushButton] = []
        for row, (label, handler, description) in enumerate(actions):
            btn = QPushButton(label)
            btn.setMinimumWidth(260)
            btn.clicked.connect(handler)
            self.export_buttons.append(btn)
            desc = QLabel(description)
            desc.setWordWrap(True)
            grid.addWidget(btn, row, 0)
            grid.addWidget(desc, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addWidget(QLabel("Export log:"))
        self.export_log = QPlainTextEdit()
        self.export_log.setReadOnly(True)
        layout.addWidget(self.export_log, 1)
        return tab

    # ------------------------------------------------------------------ helpers
    @contextmanager
    def _busy(self, message: str):
        self.statusBar().showMessage(message)
        self.progress.show()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.hide()

    def _error(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"{title}: {message}", 10000)
        QMessageBox.warning(self, title, message)

    def _unexpected(self, action: str, exc: Exception) -> None:
        logger.exception("Unexpected error during %s", action)
        log_file = user_data_dir() / "databizex.log"
        QMessageBox.critical(
            self, "Unexpected error",
            f"Something went wrong during {action}:\n\n{exc}\n\n"
            f"The app is still usable. Details were written to:\n{log_file}",
        )
        self.statusBar().showMessage(f"Error during {action}", 10000)

    def _require_data(self) -> bool:
        if self.working_df is None:
            QMessageBox.information(self, "No data", "Open a file or load the sample data first.")
            return False
        return True

    def data_state_text(self) -> str:
        if self.working_df is None:
            return "No data loaded"
        mapped = " — columns mapped" if self.column_mapping else ""
        cleaning = []
        if self.duplicates_removed:
            cleaning.append(f"{self.duplicates_removed:,} exact duplicates removed")
        if self.normalized_columns:
            cleaning.append("text normalized in " + ", ".join(self.normalized_columns))
        if cleaning:
            return f"Cleaned working data ({'; '.join(cleaning)}){mapped}"
        return f"Original imported data (no cleaning applied){mapped}"

    def _ask_save_path(self, caption: str, default_name: str, file_filter: str) -> Path | None:
        start = str(Path.home() / "Documents" / default_name)
        path, _ = QFileDialog.getSaveFileName(self, caption, start, file_filter)
        return Path(path) if path else None

    def _ask_folder(self, caption: str) -> Path | None:
        folder = QFileDialog.getExistingDirectory(self, caption, str(Path.home() / "Documents"))
        return Path(folder) if folder else None

    def _log_export(self, text: str) -> None:
        self.export_log.appendPlainText(text)
        self.statusBar().showMessage(text, 10000)

    # ------------------------------------------------------------------ loading
    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open sales data", str(Path.home()), FILE_DIALOG_FILTER)
        if path:
            self.load_path(Path(path))

    def load_sample(self) -> bool:
        path = sample_data_path()
        if not path.is_file():
            self._error("Sample data missing", f"The bundled sample file was not found:\n{path}")
            return False
        return self.load_path(path)

    def load_path(self, path: Path) -> bool:
        try:
            with self._busy(f"Loading {path.name}..."):
                result = load_file(path)
        except DataLoadError as exc:
            # Previously loaded data (if any) stays active, so the app remains usable.
            self._error("Could not load file", f"{path.name}\n\n{exc}")
            return False
        except Exception as exc:  # noqa: BLE001 - shown to the user and logged
            self._unexpected(f"loading {path.name}", exc)
            return False

        detection = detect_formats(result.df, result.delimiter, result.encoding)
        options = detection.options
        if not detection.is_default():
            options = self._ask_import_options(path, detection)
            if (options.delimiter, options.encoding) != (result.delimiter, result.encoding):
                try:
                    with self._busy(f"Reading {path.name} with the chosen delimiter and encoding..."):
                        result = load_file(path, delimiter=options.delimiter, encoding=options.encoding)
                except DataLoadError as exc:
                    self._error("Could not load file", f"{path.name}\n\n{exc}")
                    return False
                except Exception as exc:  # noqa: BLE001
                    self._unexpected(f"loading {path.name}", exc)
                    return False
        logger.info("Import options for %s: %s", path.name, options.describe())

        self.source_path = path
        self.original_df = result.df
        self.source_rows = result.source_rows
        self.import_options = options
        self.column_mapping = self._ask_column_mapping(result.df) if needs_mapping(result.df) else None
        self._make_working_copy()
        self.duplicates_removed = 0
        self.normalized_columns = []
        self._clear_kpis()
        self._refresh_all()
        self.tabs.setCurrentIndex(0)
        if result.warnings:
            QMessageBox.information(self, "Loaded with warnings", "\n\n".join(result.warnings))
        message = f"Loaded {path.name}: {len(result.df):,} rows × {result.df.shape[1]} columns"
        # After a mapping the user has already confirmed the fields, so compute KPIs straight away.
        if self.column_mapping and self.run_kpis():
            message += " — KPIs calculated with your column mapping"
        self.statusBar().showMessage(message, 10000)
        return True

    def _ask_import_options(self, path: Path, detection: FormatDetection) -> ImportOptions:
        dialog = ImportOptionsDialog(path.name, detection, self)
        if not dialog.exec():
            logger.info("Import options declined; using the file as read, without conversion")
            read = detection.options
            return ImportOptions(delimiter=read.delimiter, encoding=read.encoding)
        return dialog.options()

    def _ask_column_mapping(self, df: pd.DataFrame) -> dict[str, str | None] | None:
        columns = list(df.columns)
        dialog = ColumnMappingDialog(columns, guess_mapping(columns), self)
        if not dialog.exec():
            logger.info("Column mapping skipped; using the file's column names as they are")
            return None
        return dialog.mapping()

    def _make_working_copy(self) -> None:
        """Set working_df from original_df: number/date conversion, then the column mapping if chosen."""
        self.unavailable_columns, self.mapping_notes = [], []
        # Indexed by source line number, which survives conversion, mapping and duplicate removal.
        indexed = with_source_rows(self.original_df, self.source_rows)
        self.conversion = apply_import_options(indexed, self.import_options)
        converted = self.conversion.df
        if self.column_mapping:
            try:
                mapped = apply_column_mapping(converted, self.column_mapping)
            except ColumnMappingError as exc:
                self.column_mapping = None
                self._error("Column mapping not applied", str(exc))
            else:
                self.working_df = mapped.df
                self.unavailable_columns, self.mapping_notes = mapped.unavailable, mapped.notes
                return
        self.working_df = converted

    # ------------------------------------------------------------------ quality
    def remove_duplicates(self) -> None:
        if not self._require_data():
            return
        try:
            with self._busy("Removing exact duplicates..."):
                cleaned, removed = remove_exact_duplicates(self.working_df)
        except Exception as exc:  # noqa: BLE001
            self._unexpected("duplicate removal", exc)
            return
        if removed == 0:
            QMessageBox.information(self, "No duplicates", "The working data has no exact duplicate rows.")
            return
        self.working_df = cleaned
        self.duplicates_removed += removed
        self._clear_kpis()
        self._refresh_all()
        self.statusBar().showMessage(
            f"Removed {removed:,} exact duplicate rows from the working copy (source file unchanged).", 10000)

    def normalize_text(self) -> None:
        if not self._require_data():
            return
        try:
            with self._busy("Looking for text variants..."):
                variants = find_text_variants(self.working_df)
        except Exception as exc:  # noqa: BLE001
            self._unexpected("text variant search", exc)
            return
        if not variants:
            QMessageBox.information(self, "Nothing to normalize",
                                    "No column has the same value written with different case or spacing.")
            return
        dialog = NormalizeDialog(variants, self)
        if not dialog.exec():
            return
        selected = dialog.selected()
        if not selected:
            self.statusBar().showMessage("No columns selected; nothing normalized.", 10000)
            return
        try:
            with self._busy("Normalizing text..."):
                normalized = apply_normalization(self.working_df, selected)
        except Exception as exc:  # noqa: BLE001
            self._unexpected("text normalization", exc)
            return
        changed = sum(g.changes for col in selected for g in variants[col])
        self.working_df = normalized
        self.normalized_columns += [c for c in selected if c not in self.normalized_columns]
        had_kpis = bool(self.kpi_results)
        self._clear_kpis()
        self._refresh_all()
        # KPIs that were on screen are recalculated so charts show the merged values.
        if had_kpis:
            self.run_kpis()
        self.statusBar().showMessage(
            f"Normalized {changed:,} values in {', '.join(selected)} (working copy only; source file unchanged).",
            10000)

    def reset_working_copy(self) -> None:
        if not self._require_data():
            return
        self._make_working_copy()  # rebuilt from the original: undoes duplicate removal and normalization
        self.duplicates_removed = 0
        self.normalized_columns = []
        self._clear_kpis()
        self._refresh_all()
        self.statusBar().showMessage("Working copy reset to the original imported data.", 10000)

    def load_rule_set(self) -> None:
        start = default_rules_path().parent
        path, _ = QFileDialog.getOpenFileName(
            self, "Load rule set", str(start if start.is_dir() else Path.home()), RULES_FILE_FILTER)
        if not path:
            return
        path = Path(path)
        try:
            rules = load_rules(path)
        except RulesError as exc:
            # The current rule set stays active.
            self._error("Could not load rule set", f"{path.name}\n\n{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self._unexpected(f"loading rule set {path.name}", exc)
            return
        self.rules, self.rules_source = rules, path.name
        self._update_rules_label()
        try:
            with self._busy("Validating with the new rule set..."):
                self._refresh_all()
        except Exception as exc:  # noqa: BLE001
            self._unexpected("validation", exc)
            return
        self.statusBar().showMessage(f"Rule set {path.name} loaded: {len(rules)} rules.", 10000)

    def _update_rules_label(self) -> None:
        self.rules_label.setText(f"Rule set: {self.rules_source} ({len(self.rules)} rules)")

    def _show_score(self, score: QualityScore | None) -> None:
        for key, title in SCORE_TITLES.items():
            value = getattr(score, key) if score is not None else None
            text = "n/a" if value is None else f"{value:.1f}" + (" / 100" if key == "overall" else "%")
            self.score_labels[key].setText(f"{title}: <b>{text}</b>")

    def _refresh_failed_rows(self) -> None:
        """Rebuild the rule dropdown from the latest validation, keeping the selection if it still applies."""
        current = self.failed_combo.currentData()
        self.failed_combo.blockSignals(True)
        self.failed_combo.clear()
        if self.quality_summary is not None:
            for key, label in failed_rule_choices(self.quality_summary, self.row_flags):
                self.failed_combo.addItem(label, key)
        index = self.failed_combo.findData(current) if current is not None else -1
        self.failed_combo.setCurrentIndex(max(index, 0))
        self.failed_combo.blockSignals(False)
        self._show_failed_rows()

    def _show_failed_rows(self) -> None:
        key = self.failed_combo.currentData()
        if key is None or self.working_df is None:
            self.failed_table.show_frame(None)
            self.failed_count_label.setText("")
            return
        table, total = failed_rows(self.working_df, self.quality_summary, self.row_flags, key,
                                   limit=FAILED_ROWS_SHOWN)
        self.failed_table.show_frame(table, float_decimals=2)
        if total > len(table):
            self.failed_count_label.setText(f"Showing {len(table):,} of {total:,} rows")
        else:
            self.failed_count_label.setText(f"Showing all {total:,} rows" if total else "No rows")

    def export_failed_rows(self) -> None:
        if not self._require_data():
            return
        key = self.failed_combo.currentData()
        if key is None:
            QMessageBox.information(self, "No failed rows", "No rule has failed rows in the working data.")
            return
        stem = "all_failed" if key == ALL_FAILED else "".join(c if c.isalnum() else "_" for c in key).strip("_")
        path = self._ask_save_path("Export failed rows (CSV)", f"{self._default_stem()}_{stem}.csv", "CSV (*.csv)")
        if not path:
            return
        if self.source_path and path.resolve() == self.source_path.resolve():
            self._error("Export blocked", "Choose a different file: the source file is never overwritten.")
            return
        table, total = failed_rows(self.working_df, self.quality_summary, self.row_flags, key)
        if self._run_export("Exporting failed rows", export_csv, table, path):
            self._log_export(f"Failed rows exported: {path} ({total:,} rows, rule: {self.failed_combo.currentText()})")
            QMessageBox.information(self, "Export complete", f"Saved {total:,} rows:\n{path}")

    # ------------------------------------------------------------------ KPIs
    def run_kpis(self) -> bool:
        if not self._require_data():
            return False
        try:
            with self._busy("Running SQL queries in DuckDB..."):
                prepared = self.db.load_sales(self.working_df, self.unavailable_columns)
                results = self.db.run_all()
                headline = self.db.headline()
        except KpiInputError as exc:
            self._error("KPIs unavailable", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001
            self._unexpected("KPI calculation", exc)
            return False

        self.kpi_results = results
        self.kpi_headline = headline
        self.kpi_basis = self.data_state_text()
        summary = validity_summary(prepared)

        self.cards["net_revenue"].set_value(_money(headline["net_revenue"]))
        self.cards["units_sold"].set_value(f"{headline['units_sold']:,.0f}")
        self.cards["gross_profit"].set_value(_money(headline["gross_profit"]))
        self.cards["gross_margin"].set_value(format_margin(headline["gross_margin"]))
        self.cards["orders"].set_value(f"{headline['orders']:,}")
        self.kpi_basis_label.setText(
            f"<b>KPIs computed on:</b> {self.kpi_basis} — {summary.total_rows:,} rows loaded into table <i>sales</i>.")
        checked = "quantity/price" if "unit_cost" in self.unavailable_columns else "quantity/price/cost"
        note = (
            f"{summary.excluded_rows:,} rows excluded from all KPIs (missing, non-numeric or negative {checked}, "
            f"or discount outside 0–100)."
        )
        if "order_date" not in self.unavailable_columns:
            note += (f" {summary.kpi_rows_without_valid_date:,} included rows have an invalid or "
                     "missing order_date and are left out of the monthly trend only.")
        self.exclusion_label.setText(" ".join([note, *self.mapping_notes]))
        self._show_selected_query()
        self._draw_charts()
        self._refresh_state_label()
        self.statusBar().showMessage("KPI queries finished.", 10000)
        return True

    def _clear_kpis(self) -> None:
        self.kpi_results = {}
        self.kpi_headline = {}
        self.kpi_basis = ""
        for card in self.cards.values():
            card.set_value("—")
        self.kpi_basis_label.setText(
            "KPIs not calculated for the current data. Click <b>Run KPI queries</b>." if self.working_df is not None
            else "Load data first.")
        self.exclusion_label.setText("")
        self.country_chart.clear("Run KPI queries to draw this chart")
        self.month_chart.clear("Run KPI queries to draw this chart")
        self._show_selected_query()

    def _show_selected_query(self) -> None:
        key = self.query_combo.currentData()
        if key is None:
            return
        self.sql_view.setPlainText(QUERIES[key].sql)
        self.result_table.show_frame(self.kpi_results.get(key), float_decimals=2)

    def _draw_charts(self) -> None:
        euro = FuncFormatter(lambda v, _pos: f"€{v / 1000:,.0f}k")

        by_country = self.kpi_results.get("by_country")
        ax = self.country_chart.ax
        ax.clear()
        ax.set_axis_on()
        if by_country is not None and not by_country.empty:
            data = by_country.sort_values("net_revenue")
            ax.barh(data["country"], data["net_revenue"], color="#2f6f9f")
            ax.xaxis.set_major_formatter(euro)
            ax.set_title("Net revenue by country")
            ax.grid(axis="x", alpha=0.3)
            self.country_chart.draw_idle()
        else:
            self.country_chart.clear("No valid rows for this chart")

        by_month = self.kpi_results.get("by_month")
        ax = self.month_chart.ax
        ax.clear()
        ax.set_axis_on()
        if by_month is not None and not by_month.empty:
            positions = list(range(len(by_month)))
            ax.plot(positions, by_month["net_revenue"], marker="o", color="#2f6f9f", linewidth=1.5)
            ax.yaxis.set_major_formatter(euro)
            ax.set_title("Net revenue by month (valid dates only)")
            step = max(1, len(by_month) // 8)
            ticks = list(range(0, len(by_month), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels([by_month["month"].iloc[i] for i in ticks], rotation=45, ha="right")
            ax.grid(alpha=0.3)
            self.month_chart.draw_idle()
        elif "order_date" in self.unavailable_columns:
            self.month_chart.clear("Monthly trend: n/a (order_date not available)")
        else:
            self.month_chart.clear("No rows with valid dates")

    # ------------------------------------------------------------------ exports
    def _run_export(self, description: str, func, *args) -> object | None:
        try:
            with self._busy(f"{description}..."):
                return func(*args)
        except ExportError as exc:
            self._error("Export failed", str(exc))
        except KpiInputError as exc:
            self._error("Export unavailable", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._unexpected(description.lower(), exc)
        return None

    def _default_stem(self) -> str:
        stem = self.source_path.stem if self.source_path else "data"
        return f"{stem}_working"

    def export_working_csv(self) -> None:
        if not self._require_data():
            return
        path = self._ask_save_path("Export working data (CSV)", f"{self._default_stem()}.csv", "CSV (*.csv)")
        if path and self.source_path and path.resolve() == self.source_path.resolve():
            self._error("Export blocked", "Choose a different file: the source file is never overwritten.")
            return
        if path and self._run_export("Exporting CSV", export_csv, self.working_df, path):
            self._log_export(f"CSV exported: {path} ({len(self.working_df):,} rows)")
            QMessageBox.information(self, "Export complete", f"Saved:\n{path}")

    def export_working_excel(self) -> None:
        if not self._require_data():
            return
        path = self._ask_save_path("Export working data (Excel)", f"{self._default_stem()}.xlsx", "Excel (*.xlsx)")
        if path and self.source_path and path.resolve() == self.source_path.resolve():
            self._error("Export blocked", "Choose a different file: the source file is never overwritten.")
            return
        if path:
            written = self._run_export("Exporting Excel", export_excel, self.working_df, path, "working_data")
            if written:
                self._log_export(f"Excel exported: {written} ({len(self.working_df):,} rows)")
                QMessageBox.information(self, "Export complete", f"Saved:\n{written}")

    def export_quality_report(self) -> None:
        if not self._require_data():
            return
        path = self._ask_save_path("Export quality report (CSV)", "quality_report.csv", "CSV (*.csv)")
        if path:
            report = run_quality_checks(self.working_df, self.rules)
            if self._run_export("Exporting quality report", export_csv, report, path):
                self._log_export(f"Quality report exported: {path}")
                QMessageBox.information(self, "Export complete", f"Saved:\n{path}")

    def export_kpi_tables(self) -> None:
        if not self._require_data():
            return
        if not self.kpi_results:
            QMessageBox.information(self, "No KPI results",
                                    "Run the KPI queries on the SQL & KPIs tab first.")
            return
        folder = self._ask_folder("Choose a folder for the KPI tables")
        if not folder:
            return
        headline = pd.DataFrame(
            [{"kpi": k, "value": v} for k, v in self.kpi_headline.items()]
            + [{"kpi": "data_basis", "value": self.kpi_basis}]
        )
        tables = {"kpi_headline": headline, **{f"kpi_{k}": self.kpi_results[k] for k in KPI_TABLE_KEYS}}
        written = self._run_export("Exporting KPI tables", export_tables_csv, tables, folder)
        if written:
            self._log_export(f"KPI tables exported to {folder}: " + ", ".join(p.name for p in written))
            QMessageBox.information(self, "Export complete", f"{len(written)} files saved in:\n{folder}")

    def export_powerbi(self) -> None:
        if not self._require_data():
            return
        folder = self._ask_folder("Choose a folder for the Power BI package")
        if not folder:
            return
        source = self.source_path.name if self.source_path else ""
        written = self._run_export("Generating Power BI package", generate_powerbi_package,
                                   self.working_df, folder, source, self.data_state_text(),
                                   self.unavailable_columns, self.import_settings_text())
        if written:
            self._log_export(f"Power BI package written to {folder}: " + ", ".join(p.name for p in written))
            QMessageBox.information(self, "Power BI package ready",
                                    f"Files saved in:\n{folder}\n\nOpen POWER_BI_GUIDE.md for the next steps.")

    # ------------------------------------------------------------------ refresh
    def _normalized_source_columns(self) -> dict[str, str]:
        """{working column: original column} for normalized columns (names differ after a mapping)."""
        mapping = self.column_mapping or {}
        return {col: mapping.get(col) or col for col in self.normalized_columns}

    def import_settings_text(self) -> str:
        return import_summary(self.import_options, self.conversion)

    def _refresh_state_label(self) -> None:
        if self.working_df is None:
            self.state_label.setText("No data loaded. Use <b>Open file...</b> or <b>Load sample data</b>.")
            return
        original_rows = len(self.original_df)
        working_rows = len(self.working_df)
        if not self.kpi_results:
            kpi = " | KPIs: not calculated"
        elif self.kpi_basis == self.data_state_text():
            kpi = " | KPIs: same as working data"
        else:
            kpi = f" | KPIs: {self.kpi_basis}"
        # Like the column mapping, import settings are only spelled out when they differ from the default.
        settings = "" if self.import_options.is_default() else f" | <b>Import:</b> {self.import_settings_text()}"
        self.state_label.setText(
            f"<b>Source:</b> {self.source_path.name} (read-only){settings} | <b>Original:</b> {original_rows:,} rows | "
            f"<b>Working:</b> {working_rows:,} rows — {self.data_state_text()}{kpi}"
        )

    def _refresh_all(self) -> None:
        has_data = self.working_df is not None
        for widget in [self.dedupe_btn, self.normalize_btn, self.reset_btn, self.run_kpi_btn,
                       *self.export_buttons]:
            widget.setEnabled(has_data)
        self._refresh_state_label()
        self.export_failed_btn.setEnabled(has_data)
        if not has_data:
            self.import_info.setText("No file loaded.")
            self._show_score(None)
            self.quality_summary, self.row_flags = None, None
            self._refresh_failed_rows()
            self._clear_kpis()
            return

        df = self.working_df
        overview = dataset_overview(self.original_df)
        self.import_info.setText(
            f"<b>{self.source_path.name}</b> — {overview['rows']:,} rows × {overview['columns']} columns, "
            f"{overview['missing_cells']:,} missing cells (original import). "
            f"Showing the first {min(PREVIEW_ROWS, len(self.original_df))} rows."
        )
        self.preview_table.show_frame(self.original_df.head(PREVIEW_ROWS))
        self.profile_table.show_frame(profile_columns(self.original_df), float_decimals=2)
        # Uses the import-converted values (e.g. "43,92" -> 43.92), before mapping or cleaning.
        imported = self.conversion.df if self.conversion is not None else self.original_df
        self.stats_table.show_frame(numeric_summary(imported), float_decimals=2)
        summary, row_flags = validate(df, self.rules)
        self.quality_summary, self.row_flags = summary, row_flags
        self.quality_table.show_frame(summary)
        self._show_score(quality_score(self.rules, summary, row_flags))
        self._refresh_failed_rows()
        self.change_table.show_frame(change_summary(self.original_df, df, self.rules,
                                                    self._normalized_source_columns()))
        self.reset_btn.setEnabled(self.duplicates_removed > 0 or bool(self.normalized_columns))

    def show_about(self) -> None:
        QMessageBox.about(self, "About DataBizex", ABOUT_TEXT)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.db.close()
        super().closeEvent(event)
