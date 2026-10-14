import json
import os

import pytest

from app import settings
from app.paths import resource_path
from app.ui import theme


# ---------------------------------------------------------------------- palettes
def test_both_palettes_are_complete_hex_colours():
    for palette in (theme.LIGHT, theme.DARK):
        for name in theme.palette_fields():
            value = getattr(palette, name)
            assert isinstance(value, str) and len(value) == 7 and value.startswith("#"), (palette.name, name)
            int(value[1:], 16)


def test_palettes_are_distinct():
    differing = [n for n in theme.palette_fields() if getattr(theme.LIGHT, n) != getattr(theme.DARK, n)]
    assert {"window", "surface", "text", "failed_cell", "status_ok", "chart_text"} <= set(differing)
    assert theme.relative_luminance(theme.LIGHT.window) > 0.8 > 0.05 > theme.relative_luminance(theme.DARK.window)


@pytest.mark.parametrize("palette", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
@pytest.mark.parametrize("fg, bg, minimum", theme.TEXT_PAIRS)
def test_text_contrast(palette, fg, bg, minimum):
    ratio = theme.contrast_ratio(getattr(palette, fg), getattr(palette, bg))
    assert ratio >= minimum, f"{palette.name}: {fg} on {bg} is {ratio:.2f}:1"


def _distance(a: str, b: str) -> float:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5


@pytest.mark.parametrize("palette", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_cell_backgrounds_stay_distinguishable_on_alternating_rows(palette):
    cells = ["failed_cell", "status_ok", "status_issue", "status_unavailable", "selection"]
    rows = ["surface", "surface_alt"]
    for i, a in enumerate(cells):
        for b in cells[i + 1:] + rows:
            assert _distance(getattr(palette, a), getattr(palette, b)) >= 40, (palette.name, a, b)


def test_contrast_ratio_reference_values():
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_score_levels_use_the_thresholds():
    assert theme.score_level(theme.SCORE_GOOD) == "good"
    assert theme.score_level(89.9) == "fair"
    assert theme.score_level(theme.SCORE_FAIR) == "fair"
    assert theme.score_level(69.9) == "poor"
    assert theme.score_level(None) == "na"


@pytest.mark.parametrize("palette", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_stylesheet_builds_from_the_palette(palette):
    qss = theme.build_stylesheet(palette)
    assert qss.count("{") == qss.count("}")
    for colour in (palette.accent, palette.accent_hover, palette.surface, palette.text, palette.selection):
        assert colour in qss
    assert 'QPushButton[primary="true"]' in qss and 'QProgressBar[level="poor"]::chunk' in qss


def test_unknown_mode_resolves_to_light():
    assert theme.resolve("nonsense") is theme.LIGHT
    assert theme.resolve(theme.SYSTEM_MODE, app=None) is theme.LIGHT  # no hint available


# ---------------------------------------------------------------------- settings
def test_settings_roundtrip(isolated_layout_memory):
    assert settings.get(settings.THEME_KEY) is None
    settings.set_value(settings.THEME_KEY, "dark")
    settings.set_value("other", 3)
    assert settings.get(settings.THEME_KEY) == "dark"
    assert json.loads((isolated_layout_memory / "settings.json").read_text(encoding="utf-8")) == {
        "theme": "dark", "other": 3}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "", '"dark"'])
def test_corrupt_settings_are_ignored_and_healed(isolated_layout_memory, content):
    path = isolated_layout_memory / "settings.json"
    path.write_text(content, encoding="utf-8")
    assert settings.load() == {}
    assert settings.get(settings.THEME_KEY, "system") == "system"
    settings.set_value(settings.THEME_KEY, "light")
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "light"}


def test_saved_theme_mode_falls_back_to_system(isolated_layout_memory):
    from app.main import saved_theme_mode

    assert saved_theme_mode() == "system"
    settings.set_value(settings.THEME_KEY, "purple")
    assert saved_theme_mode() == "system"
    settings.set_value(settings.THEME_KEY, "dark")
    assert saved_theme_mode() == "dark"


# ---------------------------------------------------------------------- icon
def test_icon_files_exist_and_open():
    from PIL import Image

    png, ico = resource_path("assets", "icon.png"), resource_path("assets", "icon.ico")
    assert png.is_file() and ico.is_file()
    with Image.open(png) as image:
        assert image.size == (256, 256) and image.mode == "RGBA"
    with Image.open(ico) as image:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(image.info["sizes"])


# ---------------------------------------------------------------------- live switch
def test_window_switches_theme_live(kpi_csv, isolated_layout_memory, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    before = app.styleSheet()
    win = main_window.MainWindow()
    try:
        assert win.load_path(kpi_csv)
        assert win.run_kpis()
        monkeypatch.setattr(win.db, "run_all", lambda: pytest.fail("theme switch re-ran the queries"))
        dark = next(a for a in win.theme_actions.actions() if a.data() == "dark")
        dark.trigger()
        assert app.styleSheet() == theme.build_stylesheet(theme.DARK) != before
        assert theme.active() is theme.DARK and dark.isChecked()
        assert win.country_chart.figure.get_facecolor()[:3] == pytest.approx(
            tuple(int(theme.DARK.surface[i:i + 2], 16) / 255 for i in (1, 3, 5)))
        assert settings.get(settings.THEME_KEY) == "dark"
        assert win.score_bars["overall"].value.text().endswith(" / 100")  # numbers stay readable

        win.set_theme_mode("light")
        assert app.styleSheet() == theme.build_stylesheet(theme.LIGHT)
        assert next(a for a in win.theme_actions.actions() if a.data() == "light").isChecked()
    finally:
        win.close()
        app.setStyleSheet(before)
        theme.apply_theme(app, theme.LIGHT_MODE)
        app.setStyleSheet(before)
        app.processEvents()
