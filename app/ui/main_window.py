"""DataBizex main window: Import & Preview, Data Quality, SQL & KPIs, Export & Reporting."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import matplotlib

matplotlib.use("QtAgg")

import duckdb
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QFont, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__, settings
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
from app.data import fixes, layout_memory, recent_files
from app.data.change_log import KIND_NORMALIZATION, SNAPSHOT_LIMIT, ChangeEntry, ChangeLog
from app.data.display import whole_number_columns
from app.data.failed_rows import ALL_FAILED, failed_cells, failed_rule_choices, failed_rows
from app.data.loader import FILE_DIALOG_FILTER, DataLoadError, load_file, with_source_rows
from app.data.normalize import apply_normalization, find_text_variants
from app.data.profiler import dataset_overview, numeric_summary, profile_columns
from app.data.rules import (
    RULES_FILE_FILTER,
    QualityScore,
    RulesError,
    default_rules,
    Rule,
    default_rules_path,
    expand_rules,
    load_rules,
    quality_score,
    validate,
)
from app.database.duckdb_manager import QUERIES, DuckDBManager
from app.database.sql_guard import GuardError
from app.exports.exporter import (
    ExportError,
    export_csv,
    export_change_log as write_change_log,
    export_excel,
    export_tables_csv,
    generate_powerbi_package,
)
from app.paths import icon_path, messy_sample_dir, sample_data_dir, sample_data_path, user_data_dir
from app.ui import theme
from app.ui.column_mapping_dialog import ColumnMappingDialog
from app.ui.fix_dialog import FixDialog
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
CHART_HINT = "Run KPI queries to draw this chart"
# Status bar: "working" messages stay until the operation ends (then they are cleared),
# results clear after STATUS_RESULT_MS, errors after STATUS_ERROR_MS (always one line).
STATUS_RESULT_MS = 9000
STATUS_ERROR_MS = 15000
STATUS_LINE_MAX = 220
DEMO_DATA_TIP = "Loads the single-table demo (retail_sales_demo.csv). More messy sample tables: File > Open sample dataset."
MESSY_SAMPLE_TIP = ("Opens the messy sales table (synthetic, 3,000 orders with planted problems). More messy sample t"
                    "ables: File > Open sample dataset. The single-table demo: Load demo data.")
NO_RULE_SELECTED = "Pick a failing rule in the Rule list to see fix options."
RULE_PASSED = "This rule has no failing rows."
FIX_TIP = "Tip: choose one rule, then click Fix..."
CHART_MIN_HEIGHT = 320  # px; the chart column scrolls when the window is shorter than three charts
PARETO_LABELLED = 10  # Pareto x-axis labels for the top products only (all bars are drawn)
MIN_WINDOW_SIZE = (1100, 700)
KPI_TABLE_KEYS = ["by_country", "by_category", "by_month", "top_products", "pareto"]
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


def _mom_text(value: float) -> str:
    """Month-over-month (or year-over-year) change as shown in the table: "+12.4%", "-3.1%" or "n/a"."""
    return "n/a" if pd.isna(value) else f"{value:+.1f}%"


def _share_text(value: float) -> str:
    return "n/a" if pd.isna(value) else f"{value:.1f}%"


# Per query: {result column: (shown as, formatter)}; only the table view uses these.
DISPLAY_COLUMNS = {
    "by_month": {"mom_pct": ("MoM %", _mom_text), "yoy_pct": ("YoY %", _mom_text)},
    "pareto": {"share_pct": ("Share %", _share_text), "cum_share_pct": ("Cumulative %", _share_text),
               "in_top_80": ("Top 80%", lambda v: "Yes" if v == 1 else "")},
}


def _display_frame(key: str, frame: pd.DataFrame | None) -> pd.DataFrame | None:
    """Query result as shown in the SQL & KPIs table (the stored and exported results keep raw numbers)."""
    if frame is None:
        return frame
    columns = {col: spec for col, spec in DISPLAY_COLUMNS.get(key, {}).items() if col in frame.columns}
    if columns:
        frame = frame.assign(**{col: frame[col].map(fmt) for col, (_name, fmt) in columns.items()})
        frame = frame.rename(columns={col: name for col, (name, _fmt) in columns.items()})
    return frame


def pareto_summary(pareto: pd.DataFrame | None) -> str:
    """ "N of M products make 80% of net revenue." ("" without products)."""
    if pareto is None or pareto.empty:
        return ""
    return f"{int(pareto['in_top_80'].sum()):,} of {len(pareto):,} products make 80% of net revenue."


def _first_line(text: object) -> str:
    """The first non-empty line of a message, without a "Traceback ..." block."""
    for line in str(text).splitlines():
        line = line.strip()
        if line and not line.startswith("Traceback"):
            return line
    return ""


def _engine_message(exc: Exception) -> str:
    """DuckDB's message without its "LINE 1: ..." caret block, which only lines up in a monospace font."""
    return str(exc).split("\nLINE ")[0].strip()


def _short_label(text: object, length: int = 16) -> str:
    text = str(text)
    return text if len(text) <= length else text[:length - 1] + "…"


def _money(value: float) -> str:
    return "n/a" if pd.isna(value) else f"€ {value:,.2f}"


class KpiCard(QFrame):
    """A themed card: small muted caption above a large value."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("kpiCard")
        self.setMinimumWidth(170)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(4)
        caption = QLabel(title)
        theme.set_role(caption, "cardCaption")
        self.value = QLabel("—")
        theme.set_role(self.value, "cardValue")
        layout.addWidget(caption)
        layout.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class ChartCanvas(FigureCanvasQTAgg):
    def __init__(self) -> None:
        self.figure = Figure(figsize=(5, 3), tight_layout=True)
        super().__init__(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.message = ""  # the empty-state text shown, if any (redrawn on a theme switch)
        self.clear(CHART_HINT)

    def reset(self):
        """A fresh themed figure with one axes (drops a secondary axis from an earlier draw)."""
        self.figure.clear()
        theme.style_figure(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.message = ""
        return self.ax

    def clear(self, message: str) -> None:
        self.reset()
        theme.chart_message(self.ax, message)
        self.message = message
        self.draw_idle()


class ScoreBar(QWidget):
    """One quality score: title and exact value above a bar filled by the theme's thresholds."""

    def __init__(self, title: str, strong: bool = False) -> None:
        super().__init__()
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        top = QHBoxLayout()
        caption = QLabel(title)
        theme.set_role(caption, "scoreTitleStrong" if strong else "scoreTitle")
        self.value = QLabel("n/a")
        theme.set_role(self.value, "scoreValueStrong" if strong else "scoreValue")
        top.addWidget(caption)
        top.addStretch(1)
        top.addWidget(self.value)
        layout.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)  # tenths of a point
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12 if strong else 8)
        theme.set_role(self.bar, "score")
        layout.addWidget(self.bar)
        self.set_score(None, "n/a")

    def set_score(self, value: float | None, text: str) -> None:
        self.value.setText(text)
        self.bar.setValue(0 if value is None else round(max(0.0, min(100.0, value)) * 10))
        theme.set_role(self.bar, theme.score_level(value), "level")
        self.bar.setAccessibleName(f"{self.title}: {text}")


# The messy multi-table sample (sample_data/messy): file, table in manifest.json, menu label.
MESSY_SAMPLES = [
    ("retail_messy_1_stores.xlsx", "stores", "Stores"),
    ("retail_messy_2_customers.xlsx", "customers", "Customers"),
    ("retail_messy_3_products.xlsx", "products", "Products"),
    ("retail_messy_4_sales.xlsx", "sales", "Sales"),
    ("retail_messy_5_returns.xlsx", "returns", "Returns"),
    ("retail_messy_4_sales.csv", "sales", "Sales as CSV"),
]
MESSY_SALES = "retail_messy_4_sales.xlsx"  # what "Try messy sample data" opens


def messy_samples(folder: Path) -> list[tuple[str, Path]]:
    """(menu label, path) for the bundled messy sample files that exist, e.g. "Sales (3,030 rows, messy)"."""
    try:
        tables = json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("tables", {})
    except (OSError, ValueError, AttributeError):
        tables = {}  # labels without row counts
    entries = []
    for file, table, label in MESSY_SAMPLES:
        path = folder / file
        if not path.is_file():
            continue
        rows = tables.get(table, {}).get("rows") if isinstance(tables.get(table), dict) else None
        entries.append((f"{label} ({rows:,} rows, messy)" if isinstance(rows, int) else f"{label} (messy)", path))
    return entries


def _is_bundled_sample(path: Path) -> bool:
    """True for files inside the app's own sample_data folder."""
    try:
        return path.resolve().is_relative_to(sample_data_dir().resolve())
    except (OSError, ValueError):
        return False


def _empty_state(text: str) -> QWidget:
    """A centred, muted two-line hint for a tab without data."""
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    theme.set_role(label, "empty")
    return label


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1280, 820)
        self.setMinimumSize(*MIN_WINDOW_SIZE)

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
        # Every fix applied to the working copy this session (with undo); cleared on load and reset.
        self.change_log = ChangeLog()
        self.kpi_results: dict[str, pd.DataFrame] = {}
        self.kpi_headline: dict[str, float] = {}
        self.kpi_basis = ""
        self.db = DuckDBManager()
        # True while table `sales` holds the current working data (set by loading it, cleared with the KPIs).
        self.sales_current = False
        # Last successful custom SQL query and its displayed rows (None after the data changes).
        self.custom_statement: str | None = None
        self.custom_result: pd.DataFrame | None = None
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
        app = QApplication.instance()
        hints = app.styleHints() if app is not None else None
        if hints is not None and hasattr(hints, "colorSchemeChanged"):  # Windows light/dark switch
            hints.colorSchemeChanged.connect(self._on_system_colour_scheme)

    # ------------------------------------------------------------------ UI setup
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open data file...", self, shortcut="Ctrl+O", triggered=self.open_file)
        sample_action = QAction("Load &demo data", self, triggered=self.load_sample)
        sample_action.setToolTip(DEMO_DATA_TIP)
        sample_action.setStatusTip(DEMO_DATA_TIP)
        exit_action = QAction("E&xit", self, shortcut="Ctrl+Q", triggered=self.close)
        file_menu.addAction(open_action)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.setToolTipsVisible(True)
        file_menu.addAction(sample_action)
        self.sample_menu = file_menu.addMenu("Open sample &dataset")
        self.sample_menu.setToolTipsVisible(True)
        file_menu.setToolTipsVisible(True)  # explains a disabled "Open sample dataset"
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        file_menu.aboutToShow.connect(self._rebuild_recent_menu)
        file_menu.aboutToShow.connect(self._rebuild_sample_menu)
        self._rebuild_recent_menu()
        self._rebuild_sample_menu()
        edit_menu = self.menuBar().addMenu("&Edit")
        # Ctrl+Z: text editors (e.g. the SQL editor) keep their own undo while they have the focus.
        self.undo_action = QAction("&Undo last fix", self, shortcut="Ctrl+Z", triggered=self.undo_last_fix)
        self.undo_action.setEnabled(False)
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(QAction("&Reset working copy to original...", self,
                                    triggered=lambda: self.reset_working_copy(ask=True)))
        view_menu = self.menuBar().addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        self.theme_actions = QActionGroup(self)
        self.theme_actions.setExclusive(True)
        for mode, label in ((theme.LIGHT_MODE, "&Light"), (theme.DARK_MODE, "&Dark"),
                            (theme.SYSTEM_MODE, "&System (follow Windows)")):
            action = QAction(label, self, checkable=True)
            action.setData(mode)
            action.setChecked(mode == theme.active_mode())
            action.triggered.connect(lambda _checked=False, m=mode: self.set_theme_mode(m))
            self.theme_actions.addAction(action)
            theme_menu.addAction(action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction("&About DataBizex", self, triggered=self.show_about))

    def _rebuild_recent_menu(self) -> None:
        """File > Open Recent from recent_files.json; rebuilt each time the File menu opens."""
        self.recent_menu.clear()
        paths = recent_files.get_paths()
        for path in paths:
            action = QAction(path.name.replace("&", "&&"), self.recent_menu,
                             triggered=lambda _checked=False, p=path: self._open_recent(p))
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            self.recent_menu.addAction(action)
        if not paths:
            self.recent_menu.addAction(QAction("(no recent files)", self.recent_menu, enabled=False))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction(QAction("Clear recent files", self.recent_menu, enabled=bool(paths),
                                           triggered=self._clear_recent_files))

    def _rebuild_sample_menu(self) -> None:
        """File > Open sample dataset: the bundled messy tables; disabled when the folder is missing."""
        self.sample_menu.clear()
        folder = messy_sample_dir()
        entries = messy_samples(folder) if folder.is_dir() else []
        for label, path in entries:
            action = QAction(label, self.sample_menu, triggered=lambda _checked=False, p=path: self.load_path(p))
            action.setToolTip(f"{path.name}: synthetic data with planted problems. Opens like any other file.")
            self.sample_menu.addAction(action)
        enabled = bool(entries)
        self.sample_menu.menuAction().setEnabled(enabled)
        self.sample_menu.menuAction().setToolTip(
            "Synthetic, deliberately messy related tables (stores, customers, products, sales, returns)."
            if enabled else f"The sample dataset folder was not found:\n{folder}")

    def open_messy_sales_sample(self) -> bool:
        """The "Try messy sample data" button: open the messy sales table through the normal pipeline."""
        path = messy_sample_dir() / MESSY_SALES
        if not path.is_file():
            self._error("Sample data missing", f"The bundled sample file was not found:\n{path}")
            return False
        return self.load_path(path)

    def _import_empty_state(self) -> QWidget:
        """The empty Import & Preview tab: the usual hint plus a secondary "Try messy sample data" button."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        layout.addWidget(_empty_state("No data loaded.\nOpen a file or load the demo data to get started."))
        self.try_sample_btn = QPushButton("Try messy sample data")
        available = (messy_sample_dir() / MESSY_SALES).is_file()
        self.try_sample_btn.setEnabled(available)
        self.try_sample_btn.setToolTip(
            MESSY_SAMPLE_TIP if available else f"The sample dataset folder was not found:\n{messy_sample_dir()}")
        self.try_sample_btn.clicked.connect(self.open_messy_sales_sample)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.try_sample_btn)
        row.addStretch(1)
        layout.addSpacing(10)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _open_recent(self, path: Path) -> bool:
        if not path.is_file():
            recent_files.remove_path(path)  # load_path below shows the usual "File not found" error
        return self.load_path(path)

    def _clear_recent_files(self) -> None:
        recent_files.clear()
        self._rebuild_recent_menu()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        root.setContentsMargins(14, 10, 14, 8)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(icon_path())).scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel("DataBizex")
        theme.set_role(title, "title")
        subtitle = QLabel("Retail Analytics & Data Quality")
        theme.set_role(subtitle, "muted")
        self.version_label = QLabel(f"v{__version__}")
        theme.set_role(self.version_label, "muted")
        open_btn = QPushButton("Open file...")
        open_btn.setToolTip("Open a .csv or .xlsx sales export (Ctrl+O). The file is only read, never changed.")
        open_btn.clicked.connect(self.open_file)
        sample_btn = QPushButton("Load demo data")
        sample_btn.setToolTip(DEMO_DATA_TIP)
        sample_btn.clicked.connect(self.load_sample)
        about_btn = QPushButton("About")
        about_btn.setToolTip("Version and author information.")
        about_btn.clicked.connect(self.show_about)
        for button in (open_btn, sample_btn):
            theme.mark_primary(button)
        header.addWidget(logo)
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
        theme.set_role(self.state_label, "info")
        root.addWidget(self.state_label)

        self.tabs = QTabWidget()
        # Each tab shows a centred hint until data is loaded (see _refresh_all).
        self.empty_stacks: list[QStackedWidget] = []
        for build, title, hint in (
            (self._build_import_tab, "Import && Preview",
             "No data loaded.\nOpen a file or load the demo data to get started."),
            (self._build_quality_tab, "Data Quality",
             "No data to check yet.\nOpen a file or load the demo data to run the quality checks."),
            (self._build_kpi_tab, "SQL && KPIs",
             "No data loaded.\nOpen a file or load the demo data, then run the KPI queries."),
            (self._build_export_tab, "Export && Reporting",
             "Nothing to export yet.\nOpen a file or load the demo data first."),
        ):
            stack = QStackedWidget()
            stack.addWidget(self._import_empty_state() if build == self._build_import_tab else _empty_state(hint))
            stack.addWidget(build())
            self.empty_stacks.append(stack)
            self.tabs.addTab(stack, title)
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate; shown only while work runs
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self._status_kind: str | None = None  # kind of the message on screen: working / info / error / hint
        self.statusBar().messageChanged.connect(self._status_changed)
        self.tabs.currentChanged.connect(lambda _index: self.clear_working_status())
        self.show_status("Ready. Open a file or load the demo data.", "hint")

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
        self.reset_btn.clicked.connect(lambda: self.reset_working_copy(ask=True))
        self.load_rules_btn = QPushButton("Load rule set...")
        self.load_rules_btn.clicked.connect(self.load_rule_set)
        self.rules_label = QLabel()
        theme.set_role(self.rules_label, "muted")
        self._update_rules_label()
        buttons.addWidget(self.dedupe_btn)
        buttons.addWidget(self.normalize_btn)
        buttons.addWidget(self.reset_btn)
        buttons.addWidget(self.load_rules_btn)
        buttons.addWidget(self.rules_label)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        note = QLabel(
            "Checks only report problems. Rows with missing values are never deleted or filled unless you "
            "choose a fix for that rule. Removing duplicates, normalizing text and fixes change the "
            "in-memory working copy only and are logged (Change log) — the source file is never modified."
        )
        note.setWordWrap(True)
        theme.set_role(note, "muted")
        layout.addWidget(note)

        panel = QFrame()
        panel.setObjectName("scorePanel")
        panel.setToolTip(SCORE_TOOLTIP)
        scores = QHBoxLayout(panel)
        scores.setContentsMargins(16, 10, 16, 12)
        scores.setSpacing(22)
        self.score_bars: dict[str, ScoreBar] = {}
        for key, title in SCORE_TITLES.items():
            bar = ScoreBar(title, strong=key == "overall")
            self.score_bars[key] = bar
            scores.addWidget(bar, 2 if key == "overall" else 1)
        layout.addWidget(panel)
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
        theme.set_role(self.failed_count_label, "muted")
        self.export_failed_btn = QPushButton("Export failed rows to CSV...")
        self.export_failed_btn.clicked.connect(self.export_failed_rows)
        self.fix_btn = QPushButton("Fix...")
        theme.mark_primary(self.fix_btn)
        self.fix_btn.clicked.connect(lambda: self.open_fix_dialog())
        failed_top.addWidget(QLabel("Rule:"))
        failed_top.addWidget(self.failed_combo)
        failed_top.addWidget(self.fix_btn)
        failed_top.addWidget(self.failed_count_label)
        failed_top.addStretch(1)
        failed_top.addWidget(self.export_failed_btn)
        failed_layout.addLayout(failed_top)
        self.fix_hint = QLabel(FIX_TIP)
        theme.set_role(self.fix_hint, "muted")
        self.fix_hint.setVisible(False)
        failed_layout.addWidget(self.fix_hint)
        self.failed_table = DataTable()
        failed_layout.addWidget(self.failed_table)

        self.quality_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.quality_table.customContextMenuRequested.connect(self._quality_context_menu)

        # Failed rows and the change log share the lower half as two views.
        self.quality_views = QTabWidget()
        self.quality_views.addTab(failed_box, "Failed rows")
        self.quality_views.addTab(self._build_change_log_view(), "Change log")

        vertical = QSplitter(Qt.Vertical)
        vertical.addWidget(splitter)
        vertical.addWidget(self.quality_views)
        vertical.setSizes([400, 320])
        layout.addWidget(vertical, 1)
        return tab

    def _build_kpi_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        self.run_kpi_btn = QPushButton("Run KPI queries (DuckDB)")
        theme.mark_primary(self.run_kpi_btn)
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
        cards.setHorizontalSpacing(12)
        for i, card in enumerate(self.cards.values()):
            cards.addWidget(card, 0, i)
            cards.setColumnStretch(i, 1)
        layout.addLayout(cards)

        self.exclusion_label = QLabel()
        self.exclusion_label.setWordWrap(True)
        theme.set_role(self.exclusion_label, "warning")
        layout.addWidget(self.exclusion_label)

        # Left: the predefined queries and the custom SQL editor as two views, each with the
        # full height for its table. Right: the charts in a scrolling column.
        self.kpi_views = QTabWidget()
        self.kpi_views.addTab(self._build_predefined_queries(), "Predefined queries")
        self.kpi_views.addTab(self._build_custom_sql(), "Custom SQL")

        self.chart_scroll = QScrollArea()
        self.chart_scroll.setWidgetResizable(True)
        self.chart_scroll.setFrameShape(QFrame.NoFrame)
        charts = QWidget()
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(0, 0, 4, 0)
        charts_layout.setSpacing(8)
        self.country_chart = ChartCanvas()
        self.month_chart = ChartCanvas()
        self.pareto_chart = ChartCanvas()
        for chart in (self.country_chart, self.month_chart, self.pareto_chart):
            chart.setMinimumHeight(CHART_MIN_HEIGHT)
            charts_layout.addWidget(chart)
        self.chart_scroll.setWidget(charts)

        self.kpi_splitter = QSplitter(Qt.Horizontal)
        self.kpi_splitter.setChildrenCollapsible(False)
        self.kpi_splitter.addWidget(self.kpi_views)
        self.kpi_splitter.addWidget(self.chart_scroll)
        self.kpi_splitter.setStretchFactor(0, 45)
        self.kpi_splitter.setStretchFactor(1, 55)
        self.kpi_splitter.setSizes([450, 550])  # about 45/55; scaled to the real width
        layout.addWidget(self.kpi_splitter, 1)
        self._show_selected_query()
        return tab

    def _build_predefined_queries(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        self.query_combo = QComboBox()
        for key in KPI_TABLE_KEYS:
            self.query_combo.addItem(QUERIES[key].title, key)
        self.query_combo.currentIndexChanged.connect(self._show_selected_query)
        layout.addWidget(self.query_combo)
        self.pareto_label = QLabel()
        self.pareto_label.setVisible(False)
        layout.addWidget(self.pareto_label)

        self.result_table = DataTable()
        sql_box = QWidget()
        sql_layout = QVBoxLayout(sql_box)
        sql_layout.setContentsMargins(0, 4, 0, 0)
        sql_label = QLabel("SQL (standard, read-only; runs unchanged on MySQL/PostgreSQL):")
        theme.set_role(sql_label, "muted")
        sql_layout.addWidget(sql_label)
        self.sql_view = QPlainTextEdit()
        self.sql_view.setReadOnly(True)
        theme.set_role(self.sql_view, "code")
        sql_layout.addWidget(self.sql_view, 1)
        self.predefined_splitter = QSplitter(Qt.Vertical)
        self.predefined_splitter.setChildrenCollapsible(False)
        self.predefined_splitter.addWidget(self.result_table)
        self.predefined_splitter.addWidget(sql_box)
        self.predefined_splitter.setStretchFactor(0, 65)
        self.predefined_splitter.setStretchFactor(1, 35)
        self.predefined_splitter.setSizes([650, 350])  # about 65/35
        layout.addWidget(self.predefined_splitter, 1)
        return page

    def _build_custom_sql(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(6, 8, 6, 6)
        hint = QLabel("Read-only: one SELECT or WITH query on table sales.")
        theme.set_role(hint, "muted")
        page_layout.addWidget(hint)
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 4)
        self.custom_sql = QPlainTextEdit("SELECT * FROM sales LIMIT 100")
        theme.set_role(self.custom_sql, "code")
        self.custom_sql.setMinimumHeight(60)
        layout.addWidget(self.custom_sql, 1)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):  # main and keypad Enter
            shortcut = QShortcut(QKeySequence(keys), self.custom_sql)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.run_custom_sql)
        buttons = QHBoxLayout()
        self.custom_run_btn = QPushButton("Run (Ctrl+Enter)")
        theme.mark_primary(self.custom_run_btn)
        self.custom_run_btn.clicked.connect(self.run_custom_sql)
        self.custom_export_btn = QPushButton("Export result to CSV...")
        self.custom_export_btn.clicked.connect(self.export_custom_result)
        self.custom_export_btn.setEnabled(False)
        buttons.addWidget(self.custom_run_btn)
        buttons.addWidget(self.custom_export_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.custom_message = QLabel()
        self.custom_message.setWordWrap(True)
        self.custom_message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.custom_message)
        self.custom_table = DataTable()
        self.custom_splitter = QSplitter(Qt.Vertical)
        self.custom_splitter.setChildrenCollapsible(False)
        self.custom_splitter.addWidget(editor)
        self.custom_splitter.addWidget(self.custom_table)
        self.custom_splitter.setStretchFactor(0, 30)
        self.custom_splitter.setStretchFactor(1, 70)
        self.custom_splitter.setSizes([300, 700])  # about 30/70
        page_layout.addWidget(self.custom_splitter, 1)
        return page

    def _build_export_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        actions = [
            ("Export working data to CSV...", self.export_working_csv,
             "All rows and columns of the current working copy, as CSV (UTF-8)."),
            ("Export working data to Excel...", self.export_working_excel,
             "The working copy as an .xlsx workbook, plus a \"Change log\" sheet when fixes were applied."),
            ("Export quality report to CSV...", self.export_quality_report,
             "The quality summary table for the working data."),
            ("Export failed rows to CSV...", self.export_failed_rows,
             "Every working row that failed the rule chosen under Failed rows on the Data Quality tab "
             "(default: all rows failing an error rule), with source_row and failed_rules columns."),
            ("Export KPI tables to CSV...", self.export_kpi_tables,
             "Headline KPIs and every KPI result table, one CSV per table, into a folder you choose."),
            ("Generate Power BI package...", self.export_powerbi,
             "sales_clean.csv (rows valid for KPIs), data_dictionary.md, POWER_BI_GUIDE.md and "
             "package_info.txt in a folder you choose, plus change_log.csv and change_log.md when fixes "
             "were applied. No .pbix file is created."),
            ("Export change log...", self.export_change_log,
             "Every fix applied to the working copy, as a Markdown audit report or a CSV table."),
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
    def show_status(self, message: str, kind: str = "info", timeout_ms: int | None = None) -> None:
        """The one way to write to the status bar.

        kind "working": stays until the operation ends (_busy clears it in every case);
        "info": a result, cleared after STATUS_RESULT_MS; "error": cleared after STATUS_ERROR_MS;
        "hint": stays until replaced. A newer message always replaces the older one, and the
        older one's timeout cannot clear it (QStatusBar restarts its timer on every message).
        Errors are shortened to their first line, so no traceback ever reaches the status bar.
        """
        if timeout_ms is None:
            timeout_ms = {"info": STATUS_RESULT_MS, "error": STATUS_ERROR_MS}.get(kind, 0)
        text = " ".join(str(message).split()) if kind != "error" else _first_line(message)
        if len(text) > STATUS_LINE_MAX:
            text = text[:STATUS_LINE_MAX - 1] + "\u2026"
        self._status_kind = kind
        self.statusBar().showMessage(text, timeout_ms)

    def clear_working_status(self) -> None:
        """Remove a leftover "working" message (results and errors keep their own timeout)."""
        if self._status_kind == "working":
            self.statusBar().clearMessage()

    def _status_changed(self, text: str) -> None:
        if not text:
            self._status_kind = None

    @contextmanager
    def _busy(self, message: str):
        """Show a "working" message, wait cursor and progress bar while the block runs, and remove them
        however the block ends (result, nothing to do, cancel or exception)."""
        self.show_status(message, "working")
        self.progress.show()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.hide()
            self.clear_working_status()

    def _error(self, title: str, message: str) -> None:
        self.show_status(f"{title}: {message}", "error")
        QMessageBox.warning(self, title, message)

    def _unexpected(self, action: str, exc: Exception) -> None:
        logger.exception("Unexpected error during %s", action)
        log_file = user_data_dir() / "databizex.log"
        QMessageBox.critical(
            self, "Unexpected error",
            f"Something went wrong during {action}:\n\n{exc}\n\n"
            f"The app is still usable. Details were written to:\n{log_file}",
        )
        self.show_status(f"Error during {action}. Details are in the log file.", "error")

    def _require_data(self) -> bool:
        if self.working_df is None:
            QMessageBox.information(self, "No data", "Open a file or load the demo data first.")
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
        fixed = ""
        # Duplicate removal and normalization are logged too, but already named under "cleaning".
        count = sum(1 for entry in self.change_log.entries if entry.kind != KIND_NORMALIZATION)
        if count:
            fixed = f" — Working copy: {count:,} fix{'es' if count != 1 else ''} applied"
        if cleaning:
            return f"Cleaned working data ({'; '.join(cleaning)}){mapped}{fixed}"
        if fixed:
            return f"Fixed working data{mapped}{fixed}"
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
        self.show_status(text)

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
        self.statusBar().clearMessage()  # nothing from the previous file stays on screen
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

        layout = list(result.df.columns)  # the layout key: column names as first read
        memory = layout_memory.find(layout)
        detection = detect_formats(result.df, result.delimiter, result.encoding)
        options = detection.options
        options_confirmed = False
        if not detection.is_default():
            remembered = layout_memory.remembered_options(memory, detection.options)
            options, options_confirmed = self._ask_import_options(path, detection, remembered)
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
        self.column_mapping = self._ask_column_mapping(result.df, memory) if needs_mapping(result.df) else None
        self._make_working_copy()
        self.change_log.clear()
        self.change_log.preparation = self._preparation_lines()
        if options_confirmed or self.column_mapping:
            self._remember_layout(layout, memory, options if options_confirmed else None)
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
        self.show_status(message)
        # Bundled samples have their own menu entries, and a one-file build unpacks them to a new
        # temporary folder on every start, so a recent entry for one would soon be stale.
        if not _is_bundled_sample(path):
            recent_files.add_path(path)
        return True

    def _ask_import_options(self, path: Path, detection: FormatDetection,
                            remembered: ImportOptions | None = None) -> tuple[ImportOptions, bool]:
        """(options to use, whether the user confirmed them)."""
        dialog = ImportOptionsDialog(path.name, detection, self, remembered=remembered)
        if not dialog.exec():
            logger.info("Import options declined; using the file as read, without conversion")
            read = detection.options
            return ImportOptions(delimiter=read.delimiter, encoding=read.encoding), False
        return dialog.options(), True

    def _ask_column_mapping(self, df: pd.DataFrame, memory: dict | None = None) -> dict[str, str | None] | None:
        columns = list(df.columns)
        remembered = layout_memory.remembered_mapping(memory, columns)
        initial = {**guess_mapping(columns), **(remembered or {})}
        dialog = ColumnMappingDialog(columns, initial, self, remembered=remembered is not None)
        if not dialog.exec():
            logger.info("Column mapping skipped; using the file's column names as they are")
            return None
        return dialog.mapping()

    def _remember_layout(self, layout: list[str], memory: dict | None, options: ImportOptions | None) -> None:
        """Save what the user confirmed for this layout; a dialog that was declined or not shown
        keeps what was remembered before."""
        previous = memory or {}
        stored_options = (layout_memory.options_to_dict(options) if options
                          else previous.get("import_options"))
        layout_memory.remember(layout, stored_options, self.column_mapping or previous.get("column_mapping"))

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
                entry = None
                if removed:
                    rule = Rule(name="Remove exact duplicates", column="*", type="unique", params={"columns": []})
                    _same_rows, entry = fixes.run_fix(self.working_df, rule, fixes.FixAction.FIX_DUPLICATES, None,
                                                      self.rules, self.unavailable_columns)
                    entry.kind = KIND_NORMALIZATION
        except Exception as exc:  # noqa: BLE001
            self._unexpected("duplicate removal", exc)
            return
        if removed == 0:
            QMessageBox.information(self, "No duplicates", "The working data has no exact duplicate rows.")
            return
        self._apply_change(cleaned, entry,
                           f"Removed {removed:,} exact duplicate rows from the working copy (source file unchanged).",
                           duplicates_removed=self.duplicates_removed + removed, rerun_kpis=False)

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
            self.show_status("No text variants found.")
            QMessageBox.information(self, "Nothing to normalize",
                                    "No column has the same value written with different case or spacing.")
            return
        dialog = NormalizeDialog(variants, self)
        if not dialog.exec():
            return
        selected = dialog.selected()
        if not selected:
            self.show_status("No columns selected; nothing normalized.")
            return
        try:
            with self._busy("Normalizing text..."):
                normalized = apply_normalization(self.working_df, selected)
        except Exception as exc:  # noqa: BLE001
            self._unexpected("text normalization", exc)
            return
        changed = sum(g.changes for col in selected for g in variants[col])
        entry = fixes.entry_from_frames(self.working_df, normalized, "Normalize text", "NORMALIZE_TEXT",
                                        list(selected), {"columns": list(selected)})
        entry.kind = KIND_NORMALIZATION
        before, after = fixes.measure(self.working_df, self.rules, self.unavailable_columns), \
            fixes.measure(normalized, self.rules, self.unavailable_columns)
        entry.quality_score_before, entry.quality_score_after = before.quality_score, after.quality_score
        entry.valid_rows_before, entry.valid_rows_after = before.valid_rows, after.valid_rows
        # KPIs that were on screen are recalculated so charts show the merged values.
        self._apply_change(
            normalized, entry,
            f"Normalized {changed:,} values in {', '.join(selected)} (working copy only; source file unchanged).",
            normalized_columns=self.normalized_columns + [c for c in selected if c not in self.normalized_columns])

    def reset_working_copy(self, ask: bool = False) -> None:
        """Rebuild the working copy from the original import and clear the change log.

        ask: confirm first when fixes are logged (the button and the Edit menu ask)."""
        if not self._require_data():
            return
        self.clear_working_status()
        logged = len(self.change_log)
        if ask and logged:
            answer = QMessageBox.question(
                self, "Reset working copy",
                f"Reset the working copy to the original import?\n\nThis discards {logged:,} logged "
                f"fix{'es' if logged != 1 else ''} and clears the change log. The source file is not affected.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._make_working_copy()  # rebuilt from the original: undoes duplicate removal, normalization and fixes
        self.duplicates_removed = 0
        self.normalized_columns = []
        self.change_log.clear()
        self.change_log.preparation = self._preparation_lines()
        self._clear_kpis()
        self._refresh_all()
        self.show_status("Working copy reset to the original imported data.")

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
        self.show_status(f"Rule set {path.name} loaded: {len(rules)} rules.")

    def _update_rules_label(self) -> None:
        self.rules_label.setText(f"Rule set: {self.rules_source} ({len(self.rules)} rules)")

    def _show_score(self, score: QualityScore | None) -> None:
        for key in SCORE_TITLES:
            value = getattr(score, key) if score is not None else None
            text = "n/a" if value is None else f"{value:.1f}" + (" / 100" if key == "overall" else "%")
            self.score_bars[key].set_score(value, text)

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
        self._update_fix_button()
        key = self.failed_combo.currentData()
        if key is None or self.working_df is None:
            self.failed_table.show_frame(None)
            self.failed_count_label.setText("")
            return
        table, total = failed_rows(self.working_df, self.quality_summary, self.row_flags, key,
                                   limit=FAILED_ROWS_SHOWN, rules=self.rules)
        labels = table.iloc[:, 0].tolist()  # source_row, the working copy's index
        cells = failed_cells(self.working_df, self.quality_summary, self.row_flags, key, self.rules, labels)
        position = {label: i for i, label in enumerate(labels)}
        self.failed_table.show_frame(table, float_decimals=2,
                                     highlight={(position[label], col) for label, col in cells},
                                     whole_number_columns=whole_number_columns(self.working_df))
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
        table, total = failed_rows(self.working_df, self.quality_summary, self.row_flags, key,
                                   rules=self.rules)
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
                self.sales_current = True
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
        self.show_status("KPI queries finished.")
        return True

    def _clear_kpis(self) -> None:
        """Called whenever the working data changes: KPIs, table sales and the custom result are stale."""
        self.sales_current = False
        if self.custom_result is not None:
            self._clear_custom_result("The data changed. Run the query again to see current results.")
        self.kpi_results = {}
        self.kpi_headline = {}
        self.kpi_basis = ""
        for card in self.cards.values():
            card.set_value("—")
        self.kpi_basis_label.setText(
            "KPIs not calculated for the current data. Click <b>Run KPI queries</b>." if self.working_df is not None
            else "Load data first.")
        self.exclusion_label.setText("")
        for chart in (self.country_chart, self.month_chart, self.pareto_chart):
            chart.clear(CHART_HINT)
        self._show_selected_query()

    def _show_selected_query(self) -> None:
        key = self.query_combo.currentData()
        if key is None:
            return
        self.sql_view.setPlainText(QUERIES[key].sql)
        self.result_table.show_frame(_display_frame(key, self.kpi_results.get(key)), float_decimals=2)
        summary = pareto_summary(self.kpi_results.get("pareto")) if key == "pareto" else ""
        self.pareto_label.setText(f"<b>{summary}</b>")
        self.pareto_label.setVisible(bool(summary))

    # ------------------------------------------------------------------ custom SQL
    def _custom_note(self, text: str, error: bool = False, detail: str = "") -> None:
        """The line under the editor: a row count, or an error sentence plus its technical detail."""
        theme.set_role(self.custom_message, "error" if error else "muted")
        self.custom_message.setText(text + (f"\nTechnical detail: {detail}" if detail else ""))

    def _clear_custom_result(self, note: str = "") -> None:
        self.custom_statement, self.custom_result = None, None
        self.custom_table.show_frame(None)
        self.custom_export_btn.setEnabled(False)
        self._custom_note(note)

    def run_custom_sql(self) -> bool:
        """Run the editor's query (guarded, read-only) on table sales; errors are shown under the editor."""
        if self.working_df is None:
            self._custom_note("Load data first: the query runs on table sales, built from the working data.",
                              error=True)
            return False
        text = self.custom_sql.toPlainText()
        started = time.perf_counter()
        try:
            with self._busy("Running your SQL query in DuckDB..."):
                if not self.sales_current:  # the same table the KPI queries use; the cards are not touched
                    self.db.load_sales(self.working_df, self.unavailable_columns)
                    self.sales_current = True
                result, total = self.db.run_custom(text)
        except GuardError as exc:
            self._custom_note(str(exc), error=True)
            self.show_status("Query not run: only read-only SELECT queries are allowed.", "error")
            return False
        except KpiInputError as exc:
            self._custom_note("The working data could not be loaded into table sales.", error=True, detail=str(exc))
            self.show_status("Query not run: the working data could not be loaded.", "error")
            return False
        except duckdb.Error as exc:
            self._custom_note("DuckDB could not run this query. Check the table and column names and the "
                              "SQL syntax.", error=True, detail=_engine_message(exc))
            self.show_status("Query failed: check the table and column names and the SQL syntax.", "error")
            return False
        except Exception as exc:  # noqa: BLE001
            self._unexpected("running the SQL query", exc)
            return False
        elapsed = time.perf_counter() - started
        self.custom_statement, self.custom_result = text, result
        self.custom_table.show_frame(result, whole_number_columns=whole_number_columns(result))
        self.custom_export_btn.setEnabled(True)
        if total > len(result):
            self._custom_note(f"Showing first {len(result):,} of {total:,} rows.")
        else:
            self._custom_note(f"{total:,} row{'s' if total != 1 else ''}.")
        self.show_status(f"Query returned {total:,} row{'s' if total != 1 else ''} in {elapsed:.2f} s")
        return True

    def export_custom_result(self) -> None:
        """Write every row of the last successful custom query (the table shows at most 1,000)."""
        if self.custom_statement is None:
            QMessageBox.information(self, "No query result", "Run a custom SQL query first.")
            return
        path = self._ask_save_path("Export query result (CSV)", f"{self._default_stem()}_query.csv", "CSV (*.csv)")
        if not path:
            return
        if self.source_path and path.resolve() == self.source_path.resolve():
            self._error("Export blocked", "Choose a different file: the source file is never overwritten.")
            return

        def write() -> int:
            full, total = self.db.run_custom(self.custom_statement, max_rows=None)
            export_csv(full, path)
            return total

        total = self._run_export("Exporting query result", write)
        if total is not None:
            self._log_export(f"Custom SQL result exported: {path} ({total:,} rows)")
            QMessageBox.information(self, "Export complete", f"Saved {total:,} rows:\n{path}")

    def _draw_charts(self) -> None:
        """Draw the three charts from the stored KPI results in the active theme (no queries run)."""
        p = theme.active()
        euro = FuncFormatter(lambda v, _pos: f"€{v / 1000:,.0f}k")

        by_country = self.kpi_results.get("by_country")
        if by_country is not None and not by_country.empty:
            ax = self.country_chart.reset()
            data = by_country.sort_values("net_revenue")
            ax.barh(data["country"], data["net_revenue"], color=p.chart_bar)
            ax.xaxis.set_major_formatter(euro)
            ax.set_title("Net revenue by country")
            theme.style_axes(ax, value_axis="x")
            self.country_chart.draw_idle()
        else:
            self.country_chart.clear("No valid rows for this chart")

        by_month = self.kpi_results.get("by_month")
        if by_month is not None and not by_month.empty:
            ax = self.month_chart.reset()
            positions = list(range(len(by_month)))
            ax.plot(positions, by_month["net_revenue"], marker="o", markersize=4, color=p.chart_line, linewidth=1.6)
            ax.yaxis.set_major_formatter(euro)
            ax.set_title("Net revenue by month (valid dates only)")
            step = max(1, len(by_month) // 8)
            ticks = list(range(0, len(by_month), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels([by_month["month"].iloc[i] for i in ticks], rotation=45, ha="right")
            theme.style_axes(ax, value_axis="y")
            self.month_chart.draw_idle()
        elif "order_date" in self.unavailable_columns:
            self.month_chart.clear("Monthly trend: n/a (order_date not available)")
        else:
            self.month_chart.clear("No rows with valid dates")

        pareto = self.kpi_results.get("pareto")
        if pareto is not None and not pareto.empty and pareto["net_revenue"].sum() > 0:
            ax = self.pareto_chart.reset()
            positions = list(range(len(pareto)))
            ax.bar(positions, pareto["net_revenue"], color=p.chart_bar)
            ax.yaxis.set_major_formatter(euro)
            ax.set_title("Pareto: net revenue by product")
            # Every bar keeps its tick, but only the top products (highest revenue first) are named.
            ax.set_xticks(positions)
            ax.set_xticklabels([_short_label(name) if i < PARETO_LABELLED else ""
                                for i, name in enumerate(pareto["product"])],
                               rotation=35, ha="right", rotation_mode="anchor")
            theme.style_axes(ax, value_axis="y")
            share = ax.twinx()
            share.plot(positions, pareto["cum_share_pct"], marker="o", markersize=3, color=p.chart_cumulative,
                       linewidth=1.6)
            share.axhline(80, linestyle=":", color=p.chart_guide, linewidth=1.2)
            share.set_ylim(0, 105)
            share.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{v:.0f}%"))
            theme.style_secondary_axes(share)
            self.pareto_chart.draw_idle()
        else:
            self.pareto_chart.clear("No valid rows for this chart")

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
            written = self._run_export("Exporting Excel", export_excel, self.working_df, path, "working_data",
                                       self.change_log)
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
                                   self.unavailable_columns, self.import_settings_text(), self.change_log)
        if written:
            self._log_export(f"Power BI package written to {folder}: " + ", ".join(p.name for p in written))
            QMessageBox.information(self, "Power BI package ready",
                                    f"Files saved in:\n{folder}\n\nOpen POWER_BI_GUIDE.md for the next steps.")

    # ------------------------------------------------------------------ guided fixes
    def _build_change_log_view(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        top = QHBoxLayout()
        self.undo_btn = QPushButton("Undo last fix")
        self.undo_btn.setToolTip("Restore the working copy as it was before the most recent fix (Ctrl+Z).")
        self.undo_btn.clicked.connect(self.undo_last_fix)
        self.export_log_btn = QPushButton("Export change log...")
        self.export_log_btn.setToolTip("Save the log as a Markdown audit report or a CSV table.")
        self.export_log_btn.clicked.connect(self.export_change_log)
        self.change_log_note = QLabel()
        theme.set_role(self.change_log_note, "muted")
        top.addWidget(self.undo_btn)
        top.addWidget(self.export_log_btn)
        top.addWidget(self.change_log_note, 1)
        layout.addLayout(top)

        self.change_log_stack = QStackedWidget()
        self.change_log_stack.addWidget(_empty_state("No fixes applied yet.\nSelect a failed rule and choose Fix..."))
        self.change_log_table = DataTable()
        self.change_log_table.setSelectionMode(QTableView.SingleSelection)
        self.change_log_table.selectionModel().currentRowChanged.connect(lambda *_: self._show_change_entry())
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.change_detail = QLabel()
        self.change_detail.setWordWrap(True)
        self.change_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.change_samples = DataTable()
        details_layout.addWidget(self.change_detail)
        details_layout.addWidget(self.change_samples, 1)
        self.change_log_splitter = QSplitter(Qt.Horizontal)
        self.change_log_splitter.setChildrenCollapsible(False)
        self.change_log_splitter.addWidget(self.change_log_table)
        self.change_log_splitter.addWidget(details)
        self.change_log_splitter.setStretchFactor(0, 55)
        self.change_log_splitter.setStretchFactor(1, 45)
        self.change_log_splitter.setSizes([550, 450])
        self.change_log_stack.addWidget(self.change_log_splitter)
        layout.addWidget(self.change_log_stack, 1)
        return page

    def _preparation_lines(self) -> list[str]:
        """How the working copy was prepared before any fix (shown in the change log exports)."""
        lines = []
        if not self.import_options.is_default():
            lines.append(f"Import options: {self.import_settings_text()}")
        if self.column_mapping:
            mapped = ", ".join(f"{field} <- {source}" for field, source in self.column_mapping.items() if source)
            lines.append(f"Column mapping: {mapped}")
        return lines

    def _rules_by_check(self) -> dict[str, Rule]:
        return {rule.name: rule for rule in expand_rules(self.rules, self.working_df)} \
            if self.working_df is not None else {}

    def _fix_problem(self, check: str | None) -> str:
        """Why a check cannot be fixed now ("" when it can)."""
        if self.working_df is None:
            return "Load data first."
        if check is None or check == ALL_FAILED:
            return NO_RULE_SELECTED
        rule = self._rules_by_check().get(check)
        if rule is None:
            return "This check is not a rule of the current rule set."
        if not fixes.suggested_actions(rule):
            return "Outliers are warnings only: check them by hand. No automatic fix is offered."
        if self.row_flags is None or check not in self.row_flags or not bool(self.row_flags[check].any()):
            return RULE_PASSED
        return ""

    def _update_fix_button(self) -> None:
        """Fix... is enabled only for one failing rule; the tooltip and status tip say why not."""
        current = self.failed_combo.currentData()
        problem = self._fix_problem(current)
        tip = problem or "Preview and apply a fix for the selected rule."
        self.fix_btn.setEnabled(not problem)
        self.fix_btn.setToolTip(tip)
        self.fix_btn.setStatusTip(tip)
        # The tip only shows while "All failed rows" is chosen and there is a rule to pick.
        has_failing_rules = self.failed_combo.count() > 1
        self.fix_hint.setVisible(has_failing_rules and current in (None, ALL_FAILED))

    def _quality_context_menu(self, pos) -> None:
        index = self.quality_table.indexAt(pos)
        frame = self.quality_table.model_._df
        if not index.isValid() or "check" not in frame.columns:
            return
        check = str(frame["check"].iloc[index.row()])
        menu = QMenu(self)
        action = menu.addAction("Fix this rule...")
        problem = self._fix_problem(check)
        action.setEnabled(not problem)
        action.setToolTip(problem or "Preview and apply a fix for this rule.")
        action.setStatusTip(problem or "Preview and apply a fix for this rule.")
        menu.setToolTipsVisible(True)
        action.triggered.connect(lambda: self.open_fix_dialog(check))
        menu.exec(self.quality_table.viewport().mapToGlobal(pos))

    def open_fix_dialog(self, check: str | None = None) -> bool:
        """Show the Fix dialog for a failed rule (default: the one chosen under Failed rows)."""
        check = check if check is not None else self.failed_combo.currentData()
        problem = self._fix_problem(check)
        if problem:
            self.show_status(problem)
            return False
        rule = self._rules_by_check()[check]
        failed = int(self.row_flags[check].sum())
        try:
            dialog = FixDialog(self.working_df, rule, self.rules, failed, self.unavailable_columns, self)
        except Exception as exc:  # noqa: BLE001
            self._unexpected("preparing the fix", exc)
            return False
        if not dialog.exec():
            return False
        result = dialog.fix_result()
        if result is None:
            return False
        new_df, entry = result
        self._apply_change(new_df, entry,
                           f"Fix applied: {entry.rows_affected:,} row{'s' if entry.rows_affected != 1 else ''} "
                           "changed. Undo available.")
        return True

    def _apply_change(self, new_df: pd.DataFrame, entry: ChangeEntry, message: str,
                      duplicates_removed: int | None = None, normalized_columns: list[str] | None = None,
                      rerun_kpis: bool = True) -> None:
        """Make new_df the working copy, log entry with the state before it, and refresh every view."""
        state = (self.working_df, self.duplicates_removed, list(self.normalized_columns))
        self.change_log.add(entry, state)
        self.working_df = new_df
        if duplicates_removed is not None:
            self.duplicates_removed = duplicates_removed
        if normalized_columns is not None:
            self.normalized_columns = normalized_columns
        self._refresh_after_change(rerun_kpis)
        self.show_status(message)

    def _refresh_after_change(self, rerun_kpis: bool = True) -> None:
        """Quality checks, previews, profile, KPIs and table sales (DuckDB) follow the working copy."""
        had_kpis = bool(self.kpi_results)
        self._clear_kpis()  # also marks table sales stale, so the next query loads the new working copy
        self._refresh_all()
        if had_kpis and rerun_kpis:
            self.run_kpis()

    def undo_last_fix(self) -> bool:
        if self.working_df is None:
            return False
        undone = self.change_log.undo_last()
        if undone is None:
            message = ("Nothing to undo." if not len(self.change_log) else
                       f"Older fixes cannot be undone: only the last {SNAPSHOT_LIMIT} are kept. "
                       "Use Reset working copy to original to start again.")
            self.show_status(message)
            return False
        entry, (working, duplicates_removed, normalized_columns) = undone
        self.working_df = working
        self.duplicates_removed, self.normalized_columns = duplicates_removed, normalized_columns
        self._refresh_after_change()
        self.show_status(f"Undone: {entry.rule} ({entry.action}). The working copy is back to the state before "
                         "that fix.")
        return True

    def _refresh_change_log(self) -> None:
        log = self.change_log
        has_entries = len(log) > 0
        self.change_log_stack.setCurrentIndex(1 if has_entries else 0)
        self.undo_action.setEnabled(log.can_undo())
        self.undo_btn.setEnabled(log.can_undo())
        self.export_log_btn.setEnabled(has_entries)
        if not has_entries:
            self.change_log_note.setText("")
            self.change_log_table.show_frame(None)
            self.change_samples.show_frame(None)
            self.change_detail.setText("")
            return
        undoable = log.undoable_count()
        note = f"{len(log):,} change{'s' if len(log) != 1 else ''} logged this session (not saved after closing)."
        if undoable < len(log):
            note += f" Only the last {undoable} can be undone; use Reset for older ones."
        self.change_log_note.setText(note)

        def score(before, after) -> str:
            show = lambda v: "n/a" if v is None else f"{v:.1f}"  # noqa: E731
            return f"{show(before)} -> {show(after)}"

        rows = [{"#": e.id, "time": e.timestamp[11:19], "rule": e.rule, "action": e.action,
                 "rows": e.rows_affected, "cells": e.cells_changed,
                 "score": score(e.quality_score_before, e.quality_score_after)} for e in log.entries]
        self.change_log_table.show_frame(pd.DataFrame(rows))
        self.change_log_table.selectRow(len(rows) - 1)
        self._show_change_entry()

    def _show_change_entry(self) -> None:
        index = self.change_log_table.currentIndex()
        entries = self.change_log.entries
        if not index.isValid() or index.row() >= len(entries):
            self.change_detail.setText("")
            self.change_samples.show_frame(None)
            return
        e = entries[index.row()]
        parameters = ", ".join(f"{k}: {v}" for k, v in e.parameters.items()
                               if k != "kept_row_for_removed") or "none"
        rows = f"{e.rows_affected:,}" + (f" (first {len(e.source_rows):,} stored)" if e.truncated else "")
        valid = "" if e.valid_rows_before is None else \
            f"<br>Rows valid for KPIs: {e.valid_rows_before:,} -> {e.valid_rows_after:,}"
        self.change_detail.setText(
            f"<b>#{e.id} {e.rule}</b> — {e.action} ({e.kind})<br>"
            f"Columns: {', '.join(e.columns) or '(whole row)'}<br>Parameters: {parameters}<br>"
            f"Rows affected: {rows}; cells changed: {e.cells_changed:,}{valid}")
        self.change_samples.show_frame(pd.DataFrame(e.samples, columns=["source_row", "column", "before", "after"]))

    def export_change_log(self) -> None:
        if not len(self.change_log):
            QMessageBox.information(self, "No changes logged",
                                    "No fixes applied yet. Select a failed rule and choose Fix...")
            return
        start = str(Path.home() / "Documents" / f"{self._default_stem()}_change_log.md")
        path, selected = QFileDialog.getSaveFileName(self, "Export change log", start,
                                                      "Markdown report (*.md);;CSV (*.csv)")
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() not in (".md", ".csv"):
            path = path.with_suffix(".csv" if selected.startswith("CSV") else ".md")
        if self.source_path and path.resolve() == self.source_path.resolve():
            self._error("Export blocked", "Choose a different file: the source file is never overwritten.")
            return
        source = self.source_path.name if self.source_path else ""
        written = self._run_export("Exporting change log", write_change_log, self.change_log, path, source)
        if written:
            self._log_export(f"Change log exported: {written} ({len(self.change_log):,} entries)")
            QMessageBox.information(self, "Export complete", f"Saved:\n{written}")

    # ------------------------------------------------------------------ refresh
    def _normalized_source_columns(self) -> dict[str, str]:
        """{working column: original column} for normalized columns (names differ after a mapping)."""
        mapping = self.column_mapping or {}
        return {col: mapping.get(col) or col for col in self.normalized_columns}

    def import_settings_text(self) -> str:
        return import_summary(self.import_options, self.conversion)

    def _refresh_state_label(self) -> None:
        if self.working_df is None:
            self.state_label.setText("No data loaded. Use <b>Open file...</b> or <b>Load demo data</b>.")
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
        self._refresh_change_log()
        for stack in self.empty_stacks:
            stack.setCurrentIndex(1 if has_data else 0)
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
        self.preview_table.show_frame(self.original_df.head(PREVIEW_ROWS),
                                      whole_number_columns=whole_number_columns(self.original_df))
        self.profile_table.show_frame(profile_columns(self.original_df), float_decimals=2)
        # Uses the import-converted values (e.g. "43,92" -> 43.92), before mapping or cleaning.
        imported = self.conversion.df if self.conversion is not None else self.original_df
        self.stats_table.show_frame(numeric_summary(imported), float_decimals=2)
        summary, row_flags = validate(df, self.rules)
        self.quality_summary, self.row_flags = summary, row_flags
        self.quality_table.show_frame(summary)
        self._show_score(quality_score(self.rules, summary, row_flags))
        self._refresh_failed_rows()
        change = change_summary(self.original_df, df, self.rules, self._normalized_source_columns(),
                                self.column_mapping)
        self.change_table.show_frame(change, whole_number_columns=whole_number_columns(change))
        self.reset_btn.setEnabled(self.duplicates_removed > 0 or bool(self.normalized_columns)
                                  or len(self.change_log) > 0)

    def show_about(self) -> None:
        self._about_dialog().exec()

    def _about_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("About DataBizex")
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(20, 18, 20, 14)
        body = QHBoxLayout()
        body.setSpacing(18)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(icon_path())).scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignTop)
        body.addWidget(logo)
        text = QVBoxLayout()
        name = QLabel("DataBizex")
        theme.set_role(name, "title")
        version = QLabel(f"Version {__version__} — Retail Analytics & Data Quality")
        theme.set_role(version, "muted")
        about = QLabel(ABOUT_TEXT)
        about.setWordWrap(True)
        about.setMinimumWidth(360)
        for widget in (name, version, about):
            text.addWidget(widget)
        text.addStretch(1)
        body.addLayout(text, 1)
        outer.addLayout(body)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        theme.mark_primary(buttons.button(QDialogButtonBox.Ok))
        buttons.accepted.connect(dialog.accept)
        outer.addWidget(buttons)
        return dialog

    # ------------------------------------------------------------------ theme
    def set_theme_mode(self, mode: str) -> None:
        """Switch to light, dark or system, remember it, and restyle everything on screen."""
        settings.set_value(settings.THEME_KEY, mode)
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, mode)
        self._after_theme_change()

    def _on_system_colour_scheme(self, *_args) -> None:
        app = QApplication.instance()
        if app is not None and theme.active_mode() == theme.SYSTEM_MODE:
            theme.apply_theme(app, theme.SYSTEM_MODE)
            self._after_theme_change()

    def _after_theme_change(self) -> None:
        """Repaint what caches colours: charts (from the stored results, no queries run) and tables."""
        for action in self.theme_actions.actions():
            action.setChecked(action.data() == theme.active_mode())
        if self.kpi_results:
            self._draw_charts()
        else:
            for chart in (self.country_chart, self.month_chart, self.pareto_chart):
                chart.clear(chart.message or CHART_HINT)
        for table in self.findChildren(DataTable):
            table.viewport().update()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.db.close()
        super().closeEvent(event)
