"""DataBizex main window: Import & Preview, Data Quality, SQL & KPIs, Export & Reporting."""

from __future__ import annotations

import logging
import os
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
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
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
from app.data import layout_memory, recent_files
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
    default_rules_path,
    load_rules,
    quality_score,
    validate,
)
from app.database.duckdb_manager import QUERIES, DuckDBManager
from app.database.sql_guard import GuardError
from app.exports.exporter import (
    ExportError,
    export_csv,
    export_excel,
    export_tables_csv,
    generate_powerbi_package,
)
from app.paths import icon_path, sample_data_path, user_data_dir
from app.ui import theme
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
CHART_HINT = "Run KPI queries to draw this chart"
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
        sample_action = QAction("Load &sample data", self, triggered=self.load_sample)
        exit_action = QAction("E&xit", self, shortcut="Ctrl+Q", triggered=self.close)
        file_menu.addAction(open_action)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.setToolTipsVisible(True)
        file_menu.addAction(sample_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        file_menu.aboutToShow.connect(self._rebuild_recent_menu)
        self._rebuild_recent_menu()
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
        sample_btn = QPushButton("Load sample data")
        sample_btn.setToolTip("Load the bundled demo sales file to try every feature.")
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
             "No data loaded.\nOpen a file or load the sample data to get started."),
            (self._build_quality_tab, "Data Quality",
             "No data to check yet.\nOpen a file or load the sample data to run the quality checks."),
            (self._build_kpi_tab, "SQL && KPIs",
             "No data loaded.\nOpen a file or load the sample data, then run the KPI queries."),
            (self._build_export_tab, "Export && Reporting",
             "Nothing to export yet.\nOpen a file or load the sample data first."),
        ):
            stack = QStackedWidget()
            stack.addWidget(_empty_state(hint))
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
            "Checks only report problems. Rows with missing values are never deleted or filled. "
            "Removing duplicates and normalizing text change the in-memory working copy only — "
            "the source file is never modified."
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
        self.statusBar().showMessage(message, 10000)
        # The bundled sample has its own menu entry, and a one-file build unpacks it to a new
        # temporary folder on every start, so a recent entry for it would soon be stale.
        if path.resolve() != sample_data_path().resolve():
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
        self.statusBar().showMessage("KPI queries finished.", 10000)
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
        try:
            with self._busy("Running your SQL query in DuckDB..."):
                if not self.sales_current:  # the same table the KPI queries use; the cards are not touched
                    self.db.load_sales(self.working_df, self.unavailable_columns)
                    self.sales_current = True
                result, total = self.db.run_custom(text)
        except GuardError as exc:
            self._custom_note(str(exc), error=True)
            return False
        except KpiInputError as exc:
            self._custom_note("The working data could not be loaded into table sales.", error=True, detail=str(exc))
            return False
        except duckdb.Error as exc:
            self._custom_note("DuckDB could not run this query. Check the table and column names and the "
                              "SQL syntax.", error=True, detail=_engine_message(exc))
            return False
        except Exception as exc:  # noqa: BLE001
            self._unexpected("running the SQL query", exc)
            return False
        self.custom_statement, self.custom_result = text, result
        self.custom_table.show_frame(result, whole_number_columns=whole_number_columns(result))
        self.custom_export_btn.setEnabled(True)
        if total > len(result):
            self._custom_note(f"Showing first {len(result):,} of {total:,} rows.")
        else:
            self._custom_note(f"{total:,} row{'s' if total != 1 else ''}.")
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
        self.reset_btn.setEnabled(self.duplicates_removed > 0 or bool(self.normalized_columns))

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
