import os

import pytest

from app.data import recent_files

EXPECTED_ENTRIES = [
    "Stores (29 rows, messy)",
    "Customers (1,020 rows, messy)",
    "Products (123 rows, messy)",
    "Sales (3,030 rows, messy)",
    "Returns (303 rows, messy)",
    "Sales as CSV (3,030 rows, messy)",
]


@pytest.fixture
def window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    offered = []
    # The samples go through the normal pipeline, so the import and mapping dialogs are offered;
    # Cancel keeps the file loaded as read.
    monkeypatch.setattr(main_window.ImportOptionsDialog, "exec", lambda d: offered.append("import") or 0)
    monkeypatch.setattr(main_window.ColumnMappingDialog, "exec", lambda d: offered.append("mapping") or 0)
    win = main_window.MainWindow()
    yield win, main_window, offered
    win.close()
    app.processEvents()


def _entries(win):
    win._rebuild_sample_menu()  # what opening the File menu does
    return [a.text() for a in win.sample_menu.actions()]


def test_sample_menu_lists_the_bundled_tables(window):
    win, _main_window, _offered = window
    assert _entries(win) == EXPECTED_ENTRIES
    assert win.sample_menu.menuAction().isEnabled()
    customers = next(a for a in win.sample_menu.actions() if a.text().startswith("Customers"))
    customers.trigger()
    assert win.source_path.name == "retail_messy_2_customers.xlsx" and len(win.original_df) == 1020


def test_try_sample_data_opens_the_sales_table_through_the_normal_pipeline(window):
    win, _main_window, offered = window
    assert win.empty_stacks[0].currentIndex() == 0 and win.try_sample_btn.isEnabled()
    win.try_sample_btn.click()
    assert win.source_path.name == "retail_messy_4_sales.xlsx"
    assert len(win.original_df) == 3030 and win.empty_stacks[0].currentIndex() == 1
    assert "mapping" in offered  # no unit_cost, category or country: the mapping dialog is offered as usual
    assert win.quality_summary is not None and len(win.quality_summary) > 0
    # Bundled samples are never stored as recent files: they may move with the app.
    assert recent_files.get_paths() == []


def test_sample_menu_is_disabled_when_the_folder_is_missing(window, tmp_path, monkeypatch):
    win, main_window, _offered = window
    missing = tmp_path / "no_samples"
    monkeypatch.setattr(main_window, "messy_sample_dir", lambda: missing)
    assert _entries(win) == []
    assert not win.sample_menu.menuAction().isEnabled()
    assert "not found" in win.sample_menu.menuAction().toolTip()
    other = main_window.MainWindow()
    try:
        assert not other.try_sample_btn.isEnabled() and "not found" in other.try_sample_btn.toolTip()
        assert not other.open_messy_sales_sample()
    finally:
        other.close()


def test_demo_csv_is_still_available(window):
    win, _main_window, _offered = window
    assert win.load_sample()
    assert win.source_path.name == "retail_sales_demo.csv"
