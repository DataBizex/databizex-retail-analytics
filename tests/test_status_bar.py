"""Status bar messages never go stale; Fix button hints; demo vs messy sample labels."""

import os

import pandas as pd
import pytest

from app.data.loader import SOURCE_ROW


@pytest.fixture
def window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    dialogs = []

    def record(parent, title, *args, **kwargs):
        # What the status bar shows while a dialog is open.
        dialogs.append((title, parent.statusBar().currentMessage()))
        return QMessageBox.Yes

    for name in ("information", "warning", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(record))
    win = main_window.MainWindow()
    yield win, main_window, app, dialogs
    win.close()
    app.processEvents()


def _wait(app, ms):
    from PySide6.QtTest import QTest

    QTest.qWait(ms)
    app.processEvents()


# ---------------------------------------------------------------------- the helper
def test_working_message_is_cleared_on_success_exception_and_nothing_to_do(window):
    win, _main_window, _app, _dialogs = window
    with win._busy("Working on it..."):
        assert win.statusBar().currentMessage() == "Working on it..."
    assert win.statusBar().currentMessage() == ""
    with pytest.raises(RuntimeError):
        with win._busy("Working on it..."):
            raise RuntimeError("boom")
    assert win.statusBar().currentMessage() == ""
    with win._busy("Looking for something..."):
        pass  # nothing to do: the block ends without any result message
    assert "Looking" not in win.statusBar().currentMessage()


def test_result_message_auto_clears_and_a_newer_message_survives_the_old_timer(window):
    win, _main_window, app, _dialogs = window
    win.show_status("Removed 25 rows.", timeout_ms=150)
    assert win.statusBar().currentMessage() == "Removed 25 rows."
    _wait(app, 400)
    assert win.statusBar().currentMessage() == ""

    win.show_status("Old result.", timeout_ms=150)
    _wait(app, 60)
    win.show_status("Newer result.", timeout_ms=1000)
    _wait(app, 300)  # the old message's 150 ms have passed
    assert win.statusBar().currentMessage() == "Newer result."


def test_errors_are_one_line_without_traceback(window):
    win, _main_window, _app, _dialogs = window
    win.show_status("Could not load file: x.csv\n\nTraceback (most recent call last):\n  File ...", "error")
    assert win.statusBar().currentMessage() == "Could not load file: x.csv"
    win._error("Could not load file", "x.csv\n\nTechnical detail: something")
    assert "\n" not in win.statusBar().currentMessage() and "Technical" not in win.statusBar().currentMessage()


def test_tab_change_clears_only_working_messages(window):
    win, _main_window, _app, _dialogs = window
    win.show_status("Running something...", "working")
    win.tabs.setCurrentIndex(1)
    assert win.statusBar().currentMessage() == ""
    win.show_status("Fix applied.")
    win.tabs.setCurrentIndex(2)
    assert win.statusBar().currentMessage() == "Fix applied."


# ---------------------------------------------------------------------- the reported bug
def test_nothing_to_normalize_leaves_no_working_message(window):
    win, _main_window, _app, dialogs = window
    assert win.load_sample()  # the demo CSV has no text variants
    win.normalize_text()
    assert dialogs[-1][0] == "Nothing to normalize"
    assert "Looking for" not in dialogs[-1][1]  # not even while the dialog is open
    assert win.statusBar().currentMessage() == "No text variants found."


def test_reset_and_export_dialogs_never_show_a_working_message(window, monkeypatch, tmp_path):
    win, _main_window, _app, dialogs = window
    assert win.load_sample()
    win.normalize_text()
    win.change_log.add(_dummy_entry(), (win.working_df, 0, []))  # a logged change, so Reset asks first
    win.reset_working_copy(ask=True)
    assert dialogs[-1][0] == "Reset working copy"
    monkeypatch.setattr(win, "_ask_save_path", lambda *a: tmp_path / "failed.csv")
    win.export_failed_rows()
    assert dialogs[-1][0] == "Export complete"
    for title, message in dialogs:
        assert not message.endswith("..."), (title, message)  # no "Looking for...", "Exporting..." etc.


def _dummy_entry():
    from app.data.change_log import ChangeEntry

    return ChangeEntry(rule="r", action="SET_MISSING", columns=["quantity"], parameters={}, rows_affected=1,
                       cells_changed=1, source_rows=[2], source_rows_total=1, truncated=False, samples=[])


def test_load_and_reset_clear_stale_messages(window):
    win, _main_window, _app, _dialogs = window
    win.show_status("Looking for text variants...", "working")
    assert win.load_sample()
    assert win.statusBar().currentMessage().startswith("Loaded retail_sales_demo.csv")
    win.show_status("Normalizing text...", "working")
    win.reset_working_copy()
    assert win.statusBar().currentMessage() == "Working copy reset to the original imported data."


# ---------------------------------------------------------------------- Fix button hints
def test_fix_button_tooltips_and_hint(window):
    win, main_window, _app, _dialogs = window
    assert win.load_sample()
    win.failed_combo.setCurrentIndex(win.failed_combo.findData(main_window.ALL_FAILED))
    assert not win.fix_btn.isEnabled()
    assert win.fix_btn.toolTip() == win.fix_btn.statusTip() == main_window.NO_RULE_SELECTED
    assert not win.fix_hint.isHidden() and win.fix_hint.text() == "Tip: choose one rule, then click Fix..."

    win.failed_combo.setCurrentIndex(win.failed_combo.findData("Negative values: quantity"))
    assert win.fix_btn.isEnabled() and win.fix_btn.toolTip() == "Preview and apply a fix for the selected rule."
    assert win.fix_hint.isHidden()  # hidden once a rule is chosen

    # A rule that passes (it is not in the dropdown, which lists failing rules only).
    assert win._fix_problem("Missing values: order_id") == main_window.RULE_PASSED == "This rule has no failing rows."


def test_fix_hint_hidden_without_failed_rules(window):
    win, _main_window, _app, _dialogs = window
    clean = pd.DataFrame({"order_id": ["A1", "A2"], "quantity": [1, 2]},
                         index=pd.Index([2, 3], name=SOURCE_ROW))
    win.working_df = win.original_df = clean.reset_index(drop=True)
    win.source_path = None
    win.quality_summary, win.row_flags = None, None
    win._refresh_failed_rows()
    assert win.fix_hint.isHidden()


# ---------------------------------------------------------------------- sample data labels
def test_demo_and_messy_sample_labels(window):
    win, main_window, _app, _dialogs = window
    from PySide6.QtWidgets import QPushButton

    texts = {b.text(): b.toolTip() for b in win.findChildren(QPushButton)}
    assert texts["Load demo data"] == main_window.DEMO_DATA_TIP
    assert "retail_sales_demo.csv" in texts["Load demo data"] and "File > Open sample dataset" in texts["Load demo data"]
    assert win.try_sample_btn.text() == "Try messy sample data"
    assert "File > Open sample dataset" in win.try_sample_btn.toolTip()
    assert "Load sample data" not in texts and "Try sample data" not in texts
    from PySide6.QtGui import QAction

    actions = {a.text(): a.toolTip() for a in win.findChildren(QAction)}
    assert actions["Load &demo data"] == main_window.DEMO_DATA_TIP and "Load &sample data" not in actions
