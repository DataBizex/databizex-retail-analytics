import json
import os

import pytest

from app.data import layout_memory
from app.data.formats import ImportOptions
from app.paths import resource_path

COLUMNS = ["Bestellnummer", "Menge", "Einzelpreis"]
OPTIONS = {"delimiter": ";", "encoding": "cp1252", "decimal": ",", "thousands": ".", "date_format": "%d.%m.%Y"}
MAPPING = {"order_id": "Bestellnummer", "quantity": "Menge", "unit_price": "Einzelpreis", "unit_cost": None}


def test_roundtrip(isolated_layout_memory):
    assert layout_memory.find(COLUMNS) is None
    layout_memory.remember(COLUMNS, OPTIONS, MAPPING)
    entry = layout_memory.find(COLUMNS)
    assert entry["columns"] == COLUMNS
    assert entry["import_options"] == OPTIONS
    assert entry["column_mapping"] == MAPPING
    assert entry["saved_at"]
    assert (isolated_layout_memory / "layout_memory.json").is_file()


def test_layout_key_is_exact_and_ordered():
    layout_memory.remember(COLUMNS, OPTIONS, MAPPING)
    assert layout_memory.find(list(reversed(COLUMNS))) is None
    assert layout_memory.find(COLUMNS[:2]) is None
    assert layout_memory.find(tuple(COLUMNS)) is not None


def test_remember_replaces_existing_layout(isolated_layout_memory):
    layout_memory.remember(COLUMNS, OPTIONS, MAPPING)
    layout_memory.remember(["a", "b"], None, {"order_id": "a"})
    layout_memory.remember(COLUMNS, {**OPTIONS, "decimal": "."}, None)
    data = json.loads((isolated_layout_memory / "layout_memory.json").read_text(encoding="utf-8"))
    assert len(data) == 2
    entry = layout_memory.find(COLUMNS)
    assert entry["import_options"]["decimal"] == "."
    assert entry["column_mapping"] is None


@pytest.mark.parametrize("content", ["{not json", '{"columns": []}', "", '[1, "x", {"columns": "abc"}]'])
def test_corrupt_file_returns_none_and_is_healed(isolated_layout_memory, content):
    path = isolated_layout_memory / "layout_memory.json"
    path.write_text(content, encoding="utf-8")
    assert layout_memory.find(COLUMNS) is None
    layout_memory.remember(COLUMNS, OPTIONS, MAPPING)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [e["columns"] for e in data] == [COLUMNS]
    assert layout_memory.find(COLUMNS)["column_mapping"] == MAPPING


def test_keeps_at_most_50_entries_dropping_the_oldest(isolated_layout_memory):
    for i in range(51):
        layout_memory.remember([f"col_{i}"], None, {"order_id": f"col_{i}"})
    data = json.loads((isolated_layout_memory / "layout_memory.json").read_text(encoding="utf-8"))
    assert len(data) == layout_memory.MAX_ENTRIES == 50
    assert layout_memory.find(["col_0"]) is None
    assert layout_memory.find(["col_1"]) is not None
    assert layout_memory.find(["col_50"]) is not None


def test_remembered_options_fall_back_to_detected_for_invalid_values():
    detected = ImportOptions(delimiter=";", encoding="utf-8-sig", decimal=",", thousands=".", date_format="%d.%m.%Y")
    entry = {"import_options": {"delimiter": "#", "encoding": "cp1252", "decimal": ["bad"], "thousands": ""}}
    options = layout_memory.remembered_options(entry, detected)
    assert options == ImportOptions(delimiter=";", encoding="cp1252", decimal=",", thousands="",
                                    date_format="%d.%m.%Y")
    excel = layout_memory.remembered_options({"import_options": OPTIONS}, ImportOptions(delimiter=None, encoding=None))
    assert (excel.delimiter, excel.encoding, excel.decimal) == (None, None, ",")
    assert layout_memory.remembered_options(None, detected) is None


def test_remembered_mapping_drops_columns_not_in_file():
    entry = {"column_mapping": {"order_id": "Bestellnummer", "quantity": "Gone", "unit_cost": None}}
    assert layout_memory.remembered_mapping(entry, COLUMNS) == {"order_id": "Bestellnummer", "unit_cost": None}
    assert layout_memory.remembered_mapping({"column_mapping": None}, COLUMNS) is None


# ---------------------------------------------------------------------- the window
def test_window_prefills_mapping_dialog_from_previous_load(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    path = resource_path("sample_data", "messy webshop export de.csv")
    opened = {"options": [], "mapping": []}

    def options_exec(dialog):
        opened["options"].append((dialog.decimal.currentData(), _shows_note(dialog)))
        return 1

    def mapping_exec(dialog, change_cost):
        opened["mapping"].append((dialog.mapping(), _shows_note(dialog)))
        if change_cost:  # the user overrides a guess, so the next load can tell memory from guessing
            dialog.combos["unit_cost"].setCurrentIndex(0)  # (not available)
        return 1

    def _shows_note(dialog):
        return any(layout_memory.REMEMBERED_NOTE in label.text() for label in dialog.findChildren(QLabel))

    monkeypatch.setattr(main_window.ImportOptionsDialog, "exec", options_exec)
    win = main_window.MainWindow()
    try:
        monkeypatch.setattr(main_window.ColumnMappingDialog, "exec", lambda d: mapping_exec(d, True))
        assert win.load_path(path)
        assert win.column_mapping["unit_cost"] is None
        monkeypatch.setattr(main_window.ColumnMappingDialog, "exec", lambda d: mapping_exec(d, False))
        assert win.load_path(path)
    finally:
        win.close()
        app.processEvents()

    assert len(opened["options"]) == 2 and len(opened["mapping"]) == 2  # shown as often as before
    (first_map, first_note), (second_map, second_note) = opened["mapping"]
    assert first_map["unit_cost"] == "Einkaufspreis" and not first_note  # keyword guess
    assert second_map["unit_cost"] is None and second_note  # the remembered choice
    assert second_map == {**first_map, "unit_cost": None}
    assert opened["options"][0] == (",", False) and opened["options"][1] == (",", True)
