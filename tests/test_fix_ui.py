import os

import pandas as pd
import pytest

from app.data.fixes import FixAction


@pytest.fixture
def window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window
    from app.ui import theme

    app = QApplication.instance() or QApplication([])
    popups = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: popups.append(a[1])))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: popups.append(a[1])))
    stylesheet = app.styleSheet()
    win = main_window.MainWindow()
    yield win, main_window, app, popups
    win.close()
    theme.apply_theme(app, theme.LIGHT_MODE)
    app.setStyleSheet(stylesheet)
    app.processEvents()


def _accept_with(main_window, monkeypatch, configure=None):
    """Make FixDialog.exec() choose (optionally configure) and press Apply without showing the dialog."""
    seen = {}

    def exec_(dialog):
        if configure:
            configure(dialog)
        dialog.update_preview()
        seen["dialog"] = dialog
        seen["summary"] = dialog.summary.text()
        seen["apply_enabled"] = dialog.apply_btn.isEnabled()
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(main_window.FixDialog, "exec", exec_)
    return seen


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_fix_dialog_applies_and_logs_on_demo_data(window, monkeypatch, mode):
    win, main_window, app, popups = window
    from app.ui import theme

    theme.apply_theme(app, mode)
    assert win.load_sample()
    original = win.original_df.copy(deep=True)
    rule = "Negative values: quantity"
    win.failed_combo.setCurrentIndex(win.failed_combo.findData(rule))
    assert win.fix_btn.isEnabled()
    seen = _accept_with(main_window, monkeypatch)
    before_rows = len(win.working_df)

    assert win.open_fix_dialog()
    assert seen["apply_enabled"]
    assert seen["summary"].startswith("2 rows / 2 cells will change. Quality score: ")
    assert seen["dialog"].current_action() is FixAction.SET_MISSING  # the first suggestion
    assert len(win.change_log) == 1 and win.change_log.entries[0].rule == rule
    assert len(win.working_df) == before_rows  # set missing keeps every row
    assert win.change_log_table.model_.rowCount() == 1 and win.change_log_stack.currentIndex() == 1
    assert "Working copy: 1 fix applied" in win.state_label.text()
    assert win.statusBar().currentMessage() == "Fix applied: 2 rows changed. Undo available."
    assert int(win.row_flags[rule].sum()) == 0  # the quality checks were refreshed
    pd.testing.assert_frame_equal(win.original_df, original)  # the original import never changes
    assert win.undo_action.isEnabled() and popups == []


def test_fix_dialog_validates_constant_and_previews_mapping(window):
    win, _main_window, _app, _popups = window
    from app.data.rules import expand_rules
    from app.ui.fix_dialog import FixDialog

    assert win.load_sample()
    rules = {r.name: r for r in expand_rules(win.rules, win.working_df)}
    dialog = FixDialog(win.working_df, rules["Missing values: quantity"], win.rules, 4, (), win)
    dialog.radios[FixAction.FILL_CONSTANT].click()
    dialog.constant_edit.setText("lots")
    dialog.update_preview()
    assert "is not a number" in dialog.detail.text() and not dialog.apply_btn.isEnabled()
    dialog.constant_edit.setText("1")
    dialog.update_preview()
    assert dialog.apply_btn.isEnabled() and dialog.summary.text().startswith("4 rows / 4 cells")
    assert dialog.preview.model_.rowCount() == 4
    dialog.deleteLater()

    dialog = FixDialog(win.working_df, rules["Invalid numeric: quantity"], win.rules, 2, (), win)
    dialog.radios[FixAction.MAP_VALUES].click()
    assert not dialog.apply_btn.isEnabled()  # no replacement typed yet: nothing would change
    values = [dialog.mapping_table.item(r, 0).text() for r in range(dialog.mapping_table.rowCount())]
    assert values and dialog.mapping_table.item(0, 1).text() == "1"
    dialog.mapping_table.item(0, 2).setText("2")
    dialog.update_preview()
    assert dialog.apply_btn.isEnabled() and dialog.summary.text().startswith("1 row / 1 cell")
    dialog.deleteLater()


def test_outlier_rules_cannot_be_fixed(window):
    win, _main_window, _app, _popups = window
    assert win.load_sample()
    problem = win._fix_problem("Outliers (IQR): quantity")
    assert "warnings only" in problem
    win.failed_combo.setCurrentIndex(0)  # "All failed rows"
    assert not win.fix_btn.isEnabled() and "one failed rule" in win.fix_btn.toolTip()


def test_undo_and_reset_through_the_window(window, monkeypatch):
    win, main_window, _app, _popups = window
    from PySide6.QtWidgets import QMessageBox

    assert win.load_sample()
    start = win.working_df.copy(deep=True)
    _accept_with(main_window, monkeypatch)
    assert win.open_fix_dialog("Negative values: quantity")
    after_first = win.working_df.copy(deep=True)
    win.remove_duplicates()  # the existing button is logged too
    assert [e.kind for e in win.change_log.entries] == ["fix", "normalization"]
    assert win.duplicates_removed == 25

    assert win.undo_last_fix()
    pd.testing.assert_frame_equal(win.working_df, after_first)
    assert win.duplicates_removed == 0
    assert win.undo_last_fix()
    pd.testing.assert_frame_equal(win.working_df, start)
    assert not win.undo_last_fix() and win.statusBar().currentMessage() == "Nothing to undo."

    assert win.open_fix_dialog("Negative values: quantity")
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.No))
    win.reset_working_copy(ask=True)
    assert len(win.change_log) == 1 and "discards 1 logged fix" in asked[0]  # No keeps everything
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win.reset_working_copy(ask=True)
    assert len(win.change_log) == 0 and win.change_log_stack.currentIndex() == 0
    assert "fix" not in win.state_label.text().lower()


def test_status_bar_after_custom_sql(window):
    win, _main_window, _app, _popups = window
    assert win.load_sample()
    win.custom_sql.setPlainText("SELECT * FROM sales LIMIT 120")
    assert win.run_custom_sql()
    message = win.statusBar().currentMessage()
    assert message.startswith("Query returned 120 rows in ") and message.endswith(" s")
    win.custom_sql.setPlainText("SELECT nope FROM sales")
    assert not win.run_custom_sql()
    assert win.statusBar().currentMessage().startswith("Query failed:")
    win.custom_sql.setPlainText("DELETE FROM sales")
    assert not win.run_custom_sql()
    assert win.statusBar().currentMessage().startswith("Query not run:")
    assert "Running" not in win.statusBar().currentMessage()
