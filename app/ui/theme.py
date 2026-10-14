"""Light and dark themes: the single source of truth for every colour in the app.

A Palette holds hex colours only. build_stylesheet() turns the active palette
into the application's Qt style sheet, build_qpalette() into a QPalette (so
native parts such as menus and scroll bars follow), and style_chart() applies
it to a Matplotlib figure. Widgets never hard-code colours: they get a role
via set_role() (a dynamic property the style sheet selects on) or read
active() when they paint, so a theme switch restyles everything live.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

LIGHT_MODE, DARK_MODE, SYSTEM_MODE = "light", "dark", "system"
MODES = (LIGHT_MODE, DARK_MODE, SYSTEM_MODE)

# Quality score bars: fill colour by value (scores are 0-100).
SCORE_GOOD = 90.0  # >= good (green)
SCORE_FAIR = 70.0  # >= fair (amber); below is poor (red)

UI_FONT = "Segoe UI"
UI_FONT_SIZE = 9
MONO_FONTS = '"Cascadia Mono", Consolas, "Courier New", monospace'


@dataclass(frozen=True)
class Palette:
    name: str
    # accent (primary buttons, selection, focus)
    accent: str
    accent_hover: str
    accent_pressed: str
    on_accent: str
    # surfaces and text
    window: str
    surface: str
    surface_alt: str  # alternating table rows
    header: str  # table headers, tab bar
    info: str  # source-info line, score panel
    border: str
    text: str
    text_muted: str
    text_disabled: str
    selection: str
    selection_text: str
    warning_text: str
    error_text: str
    # table cell backgrounds (text stays `text`)
    status_ok: str
    status_issue: str
    status_unavailable: str
    failed_cell: str
    # quality score bars
    score_good: str
    score_fair: str
    score_poor: str
    score_track: str
    # charts
    chart_bar: str
    chart_line: str
    chart_cumulative: str
    chart_guide: str
    chart_grid: str
    chart_text: str


LIGHT = Palette(
    name=LIGHT_MODE,
    accent="#1f4e8c", accent_hover="#2a5fa5", accent_pressed="#173c6c", on_accent="#ffffff",
    window="#f2f4f7", surface="#ffffff", surface_alt="#f3f6fa", header="#e8edf3", info="#e7eef7",
    border="#c9d1dc", text="#1b2430", text_muted="#555f6e", text_disabled="#7a8390",
    selection="#1f4e8c", selection_text="#ffffff", warning_text="#8a4b00", error_text="#a61b1b",
    status_ok="#b5e6be", status_issue="#fbe3a9", status_unavailable="#cfd3d9", failed_cell="#f6c4c4",
    score_good="#2e8b57", score_fair="#d4900a", score_poor="#c63a2f", score_track="#dfe5ec",
    chart_bar="#2b5fa3", chart_line="#2b5fa3", chart_cumulative="#d06a1f", chart_guide="#6b7482",
    chart_grid="#cfd6df", chart_text="#2b3440",
)

DARK = Palette(
    name=DARK_MODE,
    accent="#2d68ba", accent_hover="#3573c9", accent_pressed="#22579f", on_accent="#ffffff",
    window="#15181d", surface="#1d2127", surface_alt="#252a31", header="#272c34", info="#1f2937",
    border="#3b424d", text="#e6e9ee", text_muted="#a7b0bd", text_disabled="#717a87",
    selection="#2d68ba", selection_text="#ffffff", warning_text="#f2b45e", error_text="#ff8f87",
    status_ok="#1f5a33", status_issue="#5b3a12", status_unavailable="#434a56", failed_cell="#7a2b31",
    score_good="#3fb370", score_fair="#e3a334", score_poor="#e5584f", score_track="#2e353f",
    chart_bar="#5b9be6", chart_line="#5b9be6", chart_cumulative="#f3a25b", chart_guide="#9aa4b2",
    chart_grid="#3a414b", chart_text="#d5dae1",
)

PALETTES = {LIGHT_MODE: LIGHT, DARK_MODE: DARK}
_active: Palette = LIGHT
_mode: str = LIGHT_MODE


def active() -> Palette:
    return _active


def active_mode() -> str:
    """The chosen mode (light, dark or system), not the resolved palette."""
    return _mode


def palette_fields() -> list[str]:
    return [f.name for f in fields(Palette) if f.name != "name"]


# ---------------------------------------------------------------------- contrast
def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of "#rrggbb"."""
    value = hex_colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Every foreground/background pair the app draws text with, and the minimum contrast it needs:
# 4.5 (WCAG AA body text), 3.0 for the disabled state (visible but muted).
TEXT_PAIRS: list[tuple[str, str, float]] = [
    ("text", "window", 4.5), ("text", "surface", 4.5), ("text", "surface_alt", 4.5),
    ("text", "header", 4.5), ("text", "info", 4.5),
    ("text_muted", "window", 4.5), ("text_muted", "surface", 4.5), ("text_muted", "info", 4.5),
    ("text_muted", "surface_alt", 4.5),
    ("on_accent", "accent", 4.5), ("on_accent", "accent_hover", 4.5), ("on_accent", "accent_pressed", 4.5),
    ("selection_text", "selection", 4.5),
    ("warning_text", "surface", 4.5), ("warning_text", "window", 4.5),
    ("error_text", "surface", 4.5), ("error_text", "window", 4.5),
    ("text", "status_ok", 4.5), ("text", "status_issue", 4.5), ("text", "status_unavailable", 4.5),
    ("text", "failed_cell", 4.5),
    ("chart_text", "surface", 4.5),
    ("text_disabled", "surface", 3.0), ("text_disabled", "window", 3.0),
]


def score_level(value: float | None) -> str:
    """good / fair / poor by the SCORE_* thresholds; "na" without a value."""
    if value is None:
        return "na"
    return "good" if value >= SCORE_GOOD else "fair" if value >= SCORE_FAIR else "poor"


# ---------------------------------------------------------------------- Qt
def build_stylesheet(p: Palette) -> str:
    """The application style sheet for palette p."""
    return f"""
QWidget {{ color: {p.text}; }}
QMainWindow, QDialog {{ background: {p.window}; }}
QToolTip {{ color: {p.text}; background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}

QLabel[role="muted"] {{ color: {p.text_muted}; }}
QLabel[role="warning"] {{ color: {p.warning_text}; }}
QLabel[role="error"] {{ color: {p.error_text}; }}
QLabel[role="title"] {{ font-size: 16pt; font-weight: 600; }}
QLabel[role="info"] {{ background: {p.info}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 6px; padding: 6px 10px; }}
QLabel[role="empty"] {{ color: {p.text_muted}; font-size: 11pt; }}
QLabel[role="dialogTitle"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="strong"] {{ font-weight: 600; }}

QFrame#kpiCard {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; }}
QFrame#kpiCard QLabel {{ background: transparent; }}
QLabel[role="cardCaption"] {{ color: {p.text_muted}; font-size: 8.5pt; }}
QLabel[role="cardValue"] {{ font-size: 17pt; font-weight: 600; }}

QFrame#scorePanel {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; }}
QFrame#scorePanel QLabel {{ background: transparent; }}
QLabel[role="scoreTitle"] {{ color: {p.text_muted}; }}
QLabel[role="scoreTitleStrong"] {{ font-weight: 600; }}
QLabel[role="scoreValue"] {{ font-weight: 600; }}
QLabel[role="scoreValueStrong"] {{ font-weight: 700; font-size: 11pt; }}
QProgressBar[role="score"] {{ background: {p.score_track}; border: none; border-radius: 4px; }}
QProgressBar[level="good"]::chunk {{ background: {p.score_good}; border-radius: 4px; }}
QProgressBar[level="fair"]::chunk {{ background: {p.score_fair}; border-radius: 4px; }}
QProgressBar[level="poor"]::chunk {{ background: {p.score_poor}; border-radius: 4px; }}
QProgressBar[level="na"]::chunk {{ background: transparent; }}

QPushButton {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 5px; padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {p.accent_hover}; background: {p.surface_alt}; }}
QPushButton:pressed {{ background: {p.header}; }}
QPushButton:focus {{ border: 1px solid {p.accent}; }}
QPushButton:disabled {{ color: {p.text_disabled}; background: {p.window}; border-color: {p.border}; }}
QPushButton[primary="true"], QDialog QPushButton:default {{
    background: {p.accent}; color: {p.on_accent}; border: 1px solid {p.accent}; font-weight: 600; }}
QPushButton[primary="true"]:hover, QDialog QPushButton:default:hover {{
    background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[primary="true"]:pressed, QDialog QPushButton:default:pressed {{
    background: {p.accent_pressed}; border-color: {p.accent_pressed}; }}
QPushButton[primary="true"]:focus, QDialog QPushButton:default:focus {{ border: 2px solid {p.text}; }}
QPushButton[primary="true"]:disabled {{ background: {p.header}; color: {p.text_disabled};
    border-color: {p.border}; }}

QLineEdit, QPlainTextEdit, QComboBox {{ background: {p.surface}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 4px; padding: 3px 6px;
    selection-background-color: {p.selection}; selection-color: {p.selection_text}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {p.accent}; }}
QComboBox:disabled, QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {p.text_disabled}; }}
QComboBox QAbstractItemView {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    selection-background-color: {p.selection}; selection-color: {p.selection_text}; }}
QPlainTextEdit[role="code"] {{ font-family: {MONO_FONTS}; font-size: 9pt; }}

QGroupBox {{ border: 1px solid {p.border}; border-radius: 6px; margin-top: 12px; padding: 8px 6px 6px 6px;
    background: {p.surface}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.text};
    font-weight: 600; }}

QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 6px; background: {p.window}; top: -1px; }}
QTabBar::tab {{ background: {p.header}; color: {p.text_muted}; border: 1px solid {p.border};
    border-bottom: none; padding: 6px 16px; margin-right: 2px;
    border-top-left-radius: 5px; border-top-right-radius: 5px; }}
QTabBar::tab:selected {{ background: {p.window}; color: {p.text}; font-weight: 600;
    border-top: 2px solid {p.accent}; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}
QTabBar::tab:focus {{ text-decoration: underline; }}

QTableView {{ background: {p.surface}; alternate-background-color: {p.surface_alt}; color: {p.text};
    gridline-color: {p.border}; border: 1px solid {p.border};
    selection-background-color: {p.selection}; selection-color: {p.selection_text}; }}
QTableView:focus {{ border: 1px solid {p.accent}; }}
QHeaderView::section {{ background: {p.header}; color: {p.text}; font-weight: 600; padding: 4px 8px;
    border: none; border-right: 1px solid {p.border}; border-bottom: 1px solid {p.border}; }}
QTableCornerButton::section {{ background: {p.header}; border: none; }}

QMenuBar {{ background: {p.window}; color: {p.text}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {p.header}; border-radius: 4px; }}
QMenu {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.selection}; color: {p.selection_text}; }}
QMenu::item:disabled {{ color: {p.text_disabled}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}

QStatusBar {{ background: {p.window}; color: {p.text_muted}; border-top: 1px solid {p.border}; }}
QStatusBar QLabel {{ color: {p.text_muted}; }}
QSplitter::handle {{ background: {p.window}; }}
QProgressBar {{ background: {p.score_track}; border: 1px solid {p.border}; border-radius: 4px;
    color: {p.text}; text-align: center; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QMessageBox QLabel {{ color: {p.text}; }}
"""


def build_qpalette(p: Palette):
    from PySide6.QtGui import QColor, QPalette

    qp = QPalette()
    roles = {
        QPalette.Window: p.window, QPalette.WindowText: p.text, QPalette.Base: p.surface,
        QPalette.AlternateBase: p.surface_alt, QPalette.ToolTipBase: p.surface, QPalette.ToolTipText: p.text,
        QPalette.PlaceholderText: p.text_muted, QPalette.Text: p.text, QPalette.Button: p.surface,
        QPalette.ButtonText: p.text, QPalette.BrightText: p.error_text, QPalette.Highlight: p.selection,
        QPalette.HighlightedText: p.selection_text, QPalette.Link: p.accent_hover, QPalette.Mid: p.border,
        QPalette.Midlight: p.header, QPalette.Dark: p.border, QPalette.Light: p.surface,
    }
    for role, colour in roles.items():
        qp.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        qp.setColor(QPalette.Disabled, role, QColor(p.text_disabled))
    return qp


def system_is_dark(app) -> bool:
    """True when Qt reports a dark colour scheme; False when unknown (falls back to light)."""
    try:
        from PySide6.QtCore import Qt

        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except (AttributeError, RuntimeError):
        return False


def resolve(mode: str, app=None) -> Palette:
    if mode == SYSTEM_MODE:
        return DARK if app is not None and system_is_dark(app) else LIGHT
    return PALETTES.get(mode, LIGHT)


def apply_theme(app, mode: str) -> Palette:
    """Make mode (light, dark or system) active on app: Fusion style, palette, style sheet, font."""
    global _active, _mode
    from PySide6.QtGui import QFont

    _mode = mode if mode in MODES else SYSTEM_MODE
    _active = resolve(_mode, app)
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    app.setFont(QFont(UI_FONT, UI_FONT_SIZE))
    app.setPalette(build_qpalette(_active))
    app.setStyleSheet(build_stylesheet(_active))
    return _active


def mark_primary(button) -> None:
    """Accent styling for a dialog's or screen's main action (OK, Apply, Run, ...)."""
    set_role(button, "true", "primary")


def set_role(widget, role: str, name: str = "role") -> None:
    """Give a widget a style-sheet role (e.g. "muted", "error") and restyle it if it changed."""
    if widget.property(name) == role:
        return
    widget.setProperty(name, role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ---------------------------------------------------------------------- Matplotlib
_chart_font: str | None = None


def chart_font() -> str:
    """Segoe UI when Matplotlib can find it (Windows), else its bundled DejaVu Sans."""
    global _chart_font
    if _chart_font is None:
        from matplotlib import font_manager

        names = {f.name for f in font_manager.fontManager.ttflist}
        _chart_font = UI_FONT if UI_FONT in names else "DejaVu Sans"
    return _chart_font


def style_figure(figure, p: Palette | None = None) -> None:
    p = p or _active
    figure.set_facecolor(p.surface)


def style_axes(ax, value_axis: str = "y", p: Palette | None = None) -> None:
    """Theme colours on one axes: no top/right spines, a light dotted grid on the value axis."""
    p = p or _active
    ax.set_facecolor(p.surface)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(p.chart_grid)
    ax.tick_params(colors=p.chart_text, labelsize=8)
    for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
        label.set_fontfamily(chart_font())
    ax.title.set_color(p.chart_text)
    ax.title.set_fontfamily(chart_font())
    ax.title.set_fontsize(10)
    ax.title.set_fontweight("semibold")
    ax.grid(False)
    ax.grid(axis=value_axis, color=p.chart_grid, linestyle=":", linewidth=0.8)
    ax.set_axisbelow(True)


def style_secondary_axes(ax, p: Palette | None = None) -> None:
    """The right-hand axis of a two-axis chart (e.g. Pareto cumulative share)."""
    p = p or _active
    ax.set_facecolor("none")
    for side in ("top", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["right"].set_color(p.chart_grid)
    ax.tick_params(colors=p.chart_text, labelsize=8)
    for label in ax.get_yticklabels():
        label.set_fontfamily(chart_font())


def chart_message(ax, message: str, p: Palette | None = None) -> None:
    """A centred, muted empty-state line on an empty axes."""
    p = p or _active
    ax.set_axis_off()
    ax.set_facecolor(p.surface)
    ax.text(0.5, 0.5, message, ha="center", va="center", color=p.text_muted, fontsize=9,
            fontfamily=chart_font(), wrap=True)
