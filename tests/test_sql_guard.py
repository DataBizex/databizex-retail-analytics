import os

import duckdb
import pandas as pd
import pytest

from app.database.duckdb_manager import CUSTOM_MAX_ROWS, DuckDBManager
from app.database.sql_guard import BLOCKED_WORDS, GuardError, check_sql


# ---------------------------------------------------------------------- allowed
@pytest.mark.parametrize("sql, expected", [
    ("SELECT * FROM sales", "SELECT * FROM sales"),
    ("  select country, SUM(quantity) FROM sales GROUP BY country  ",
     "select country, SUM(quantity) FROM sales GROUP BY country"),
    ("WITH t AS (SELECT country FROM sales) SELECT * FROM t", "WITH t AS (SELECT country FROM sales) SELECT * FROM t"),
    ("SELECT 'DROP TABLE sales; DELETE' AS note", "SELECT 'DROP TABLE sales; DELETE' AS note"),
    ("SELECT 'it''s a SET' AS note", "SELECT 'it''s a SET' AS note"),
    ('SELECT quantity AS "update" FROM sales', 'SELECT quantity AS "update" FROM sales'),
    ("SELECT 1;", "SELECT 1"),
    ("SELECT 1 ;  \n", "SELECT 1"),
    ("-- monthly totals\nSELECT 1", "SELECT 1"),
    ("/* note: DROP is mentioned here */ SELECT 1 -- and DELETE here", "SELECT 1"),
    ("SELECT 1; -- done", "SELECT 1"),
    ("SELECT updated_at, created, settings FROM t", "SELECT updated_at, created, settings FROM t"),
])
def test_allowed_queries(sql, expected):
    assert check_sql(sql) == expected


# ---------------------------------------------------------------------- rejected
@pytest.mark.parametrize("word", BLOCKED_WORDS)
def test_each_blocked_word_is_rejected_case_insensitively(word):
    with pytest.raises(GuardError) as info:
        check_sql(f"SELECT * FROM sales WHERE {word.lower()} = 1")
    assert str(info.value) == (f"Only read-only SELECT queries can run here. The word {word} is not allowed.")


def test_dml_behind_with_is_rejected():
    with pytest.raises(GuardError, match="The word DELETE is not allowed"):
        check_sql("WITH t AS (SELECT 1) DELETE FROM sales")


@pytest.mark.parametrize("sql", ["SELECT 1; SELECT 2", "SELECT 1; DROP TABLE sales", "SELECT 1;;",
                                 "SELECT ';'; DELETE FROM sales"])
def test_more_than_one_statement_is_rejected(sql):
    with pytest.raises(GuardError, match="one statement at a time"):
        check_sql(sql)


@pytest.mark.parametrize("sql", ["/* x */ DROP TABLE sales", "-- just a select\nDROP TABLE sales",
                                 "DESCRIBE sales", "EXPLAIN SELECT 1", "(SELECT 1)"])
def test_statement_must_start_with_select_or_with(sql):
    with pytest.raises(GuardError, match="must start with SELECT or WITH"):
        check_sql(sql)


@pytest.mark.parametrize("sql", ["", "   ", ";", "-- only a comment", "/* nothing */ ;", None])
def test_empty_input_is_rejected(sql):
    with pytest.raises(GuardError, match="The query is empty"):
        check_sql(sql)


@pytest.mark.parametrize("sql, message", [("SELECT 'open", "single quotes is not closed"),
                                          ('SELECT "open FROM sales', "double quotes is not closed"),
                                          ("SELECT 1 /* open", "comment is not closed")])
def test_unclosed_quotes_and_comments_are_rejected(sql, message):
    with pytest.raises(GuardError, match=message):
        check_sql(sql)


# ---------------------------------------------------------------------- running on DuckDB
@pytest.fixture
def db(kpi_df):
    manager = DuckDBManager()
    manager.load_sales(kpi_df)
    yield manager
    manager.close()


def test_custom_aggregate_on_fixture(db):
    result, total = db.run_custom(
        "SELECT country, SUM(quantity) AS units, COUNT(*) AS lines FROM sales "
        "WHERE kpi_valid = 1 GROUP BY country ORDER BY units DESC;")
    assert total == 3
    assert list(result["country"]) == ["Germany", "Spain", "France"]
    assert list(result["units"]) == pytest.approx([6, 3, 1])
    assert list(result["lines"]) == [2, 1, 1]


def test_custom_query_does_not_change_sales(db):
    before = db.run("headline")
    db.run_custom("SELECT * FROM sales WHERE quantity > 100")
    with pytest.raises(GuardError):
        db.run_custom("DELETE FROM sales")
    pd.testing.assert_frame_equal(db.run("headline"), before)


def test_custom_query_errors_come_from_duckdb(db):
    with pytest.raises(duckdb.Error, match="nope"):
        db.run_custom("SELECT nope FROM sales")


def test_custom_result_is_truncated_but_counted(db):
    result, total = db.run_custom("SELECT range AS n FROM range(2500) ORDER BY n DESC")
    assert total == 2500
    assert len(result) == CUSTOM_MAX_ROWS == 1000
    assert result["n"].iloc[0] == 2499 and result["n"].iloc[-1] == 1500  # first rows, in query order
    full, total = db.run_custom("SELECT range AS n FROM range(2500)", max_rows=None)
    assert len(full) == total == 2500


def test_empty_custom_result_keeps_columns(db):
    result, total = db.run_custom("SELECT country, quantity FROM sales WHERE 1 = 0")
    assert total == 0 and list(result.columns) == ["country", "quantity"]


# ---------------------------------------------------------------------- the window
def test_window_custom_sql_editor(kpi_csv, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    popups = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: popups.append(a[1])))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: popups.append(a[1])))
    win = main_window.MainWindow()
    try:
        assert win.load_path(kpi_csv)
        # Runs without the KPI queries: table sales is loaded, the cards stay empty.
        win.custom_sql.setPlainText("SELECT order_id FROM sales ORDER BY order_id")
        assert win.run_custom_sql()
        assert list(win.custom_result["order_id"]) == ["A1", "A2", "A3", "A4", "A5"]
        assert win.custom_message.text() == "5 rows."
        assert win.cards["net_revenue"].value.text() == "—"

        win.custom_sql.setPlainText("SELECT 1; DROP TABLE sales")
        assert not win.run_custom_sql()
        assert "one statement at a time" in win.custom_message.text()
        win.custom_sql.setPlainText("SELECT nope FROM sales")
        assert not win.run_custom_sql()
        assert win.custom_message.text().startswith("DuckDB could not run this query.")
        assert "\nTechnical detail: " in win.custom_message.text()
        assert popups == []  # errors are shown inline, not in a popup
        assert win.custom_result is not None  # the last successful result is kept for export

        win.custom_sql.setPlainText("SELECT range FROM range(1500)")
        kpi_tab = next(win.tabs.widget(i) for i in range(win.tabs.count())
                       if win.tabs.widget(i).isAncestorOf(win.custom_sql))
        win.tabs.setCurrentWidget(kpi_tab)  # shortcuts only reach a visible editor in the active window
        win.kpi_views.setCurrentIndex(1)  # the Custom SQL view
        win.show()
        assert QTest.qWaitForWindowActive(win, 2000)
        win.custom_sql.setFocus()
        QTest.keyClick(win.custom_sql, Qt.Key_Return, Qt.ControlModifier)
        assert win.custom_message.text() == "Showing first 1,000 of 1,500 rows."
        assert win.custom_sql.toPlainText() == "SELECT range FROM range(1500)"  # no newline typed

        target = tmp_path / "result.csv"
        monkeypatch.setattr(win, "_ask_save_path", lambda *a: target)
        win.export_custom_result()
        assert len(pd.read_csv(target)) == 1500  # the export has every row, not only the 1,000 shown
        assert popups == ["Export complete"]

        assert win.run_kpis()
        net = win.cards["net_revenue"].value.text()
        win.custom_sql.setPlainText("SELECT SUM(quantity) AS q FROM sales")
        assert win.run_custom_sql()
        assert win.cards["net_revenue"].value.text() == net  # KPI cards unaffected

        assert win.load_path(kpi_csv)  # new working data: the old result is dropped
        assert win.custom_result is None and not win.custom_export_btn.isEnabled()
    finally:
        win.close()
        app.processEvents()
