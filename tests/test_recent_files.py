import json
import os

import pytest

from app.data import recent_files


def _touch(folder, name):
    path = folder / name
    path.write_text("order_id,quantity\nA1,1\n", encoding="utf-8")
    return path


def test_roundtrip_and_clear(tmp_path):
    first, second = _touch(tmp_path, "a.csv"), _touch(tmp_path, "b.csv")
    assert recent_files.get_paths() == []
    recent_files.add_path(first)
    recent_files.add_path(second)
    assert recent_files.get_paths() == [second.resolve(), first.resolve()]  # most recent first
    recent_files.clear()
    assert recent_files.get_paths() == []


def test_keeps_the_10_most_recent(tmp_path):
    paths = [_touch(tmp_path, f"f{i}.csv") for i in range(12)]
    for path in paths:
        recent_files.add_path(path)
    stored = recent_files.get_paths()
    assert len(stored) == recent_files.MAX_ENTRIES == 10
    assert stored[0] == paths[-1].resolve() and paths[0].resolve() not in stored and paths[1].resolve() not in stored


def test_duplicate_moves_to_front(tmp_path):
    a, b, c = (_touch(tmp_path, n) for n in ("a.csv", "b.csv", "c.csv"))
    for path in (a, b, c, a):
        recent_files.add_path(path)
    assert recent_files.get_paths() == [a.resolve(), c.resolve(), b.resolve()]


@pytest.mark.parametrize("content", ["[not json", '{"paths": []}', "", '[3, null, ""]'])
def test_corrupt_file_returns_empty_and_is_healed(isolated_layout_memory, tmp_path, content):
    stored = isolated_layout_memory / "recent_files.json"
    stored.write_text(content, encoding="utf-8")
    assert recent_files.get_paths() == []
    path = _touch(tmp_path, "a.csv")
    recent_files.add_path(path)
    assert json.loads(stored.read_text(encoding="utf-8")) == [str(path.resolve())]


def test_window_open_recent_menu(kpi_csv, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[1])))
    # Standard columns, so no dialog should open; fail instead of blocking if one does.
    monkeypatch.setattr(main_window.ImportOptionsDialog, "exec", lambda self: pytest.fail("dialog opened"))
    monkeypatch.setattr(main_window.ColumnMappingDialog, "exec", lambda self: pytest.fail("dialog opened"))
    path = kpi_csv
    win = main_window.MainWindow()
    try:
        def entries():
            win._rebuild_recent_menu()  # what opening the File menu does
            return [(a.text(), a.toolTip()) for a in win.recent_menu.actions()
                    if not a.isSeparator() and a.isEnabled() and a.text() != "Clear recent files"]

        assert entries() == []
        assert win.load_sample()
        assert entries() == []  # the bundled sample is not listed
        assert win.load_path(path)
        assert entries() == [("kpi.csv", str(path.resolve()))]

        path.unlink()  # clicking a file that is gone: friendly error, and it leaves the list
        action = next(a for a in win.recent_menu.actions() if a.text() == "kpi.csv")
        action.trigger()
        assert warnings == ["Could not load file"]
        assert entries() == []
    finally:
        win.close()
        app.processEvents()
