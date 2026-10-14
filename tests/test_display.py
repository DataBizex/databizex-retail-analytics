import pandas as pd

from app.data.display import format_whole, whole_number_columns
from app.data.failed_rows import ALL_FAILED, failed_cells
from app.data.loader import SOURCE_ROW
from app.data.rules import ALL_COLUMNS, Rule, validate


# ---------------------------------------------------------------------- failed cells
# Index = source line numbers, as in the working copy.
#   2 ok
#   3 quantity -1 AND unit_cost 50 > unit_price 20: fails two error rules at once
#   4 country missing
#   5 exact duplicate of line 2 (rule has no column: nothing highlighted)
#   6 order_date "soon" (warning only: not in "All failed rows")
RULES = [
    Rule(name="Missing values (all columns)", column=ALL_COLUMNS, type="not_null"),
    Rule(name="Negative quantity", column="quantity", type="range", params={"min": 0}),
    Rule(name="Cost above price", column="unit_cost", type="cross_column",
         params={"other": "unit_price", "operator": "<=", "compare_as": "numeric"}),
    Rule(name="Duplicate rows", column=ALL_COLUMNS, type="unique"),
    Rule(name="Invalid dates", column="order_date", type="dtype", params={"kind": "date"}, severity="warning"),
]


def _fixture():
    df = pd.DataFrame(
        {"order_date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-01", "soon"],
         "country": ["DE", "FR", None, "DE", "ES"],
         "quantity": [1, -1, 2, 1, 3],
         "unit_price": [10, 20, 10, 10, 10],
         "unit_cost": [5, 50, 5, 5, 5]},
        index=pd.Index([2, 3, 4, 5, 6], name=SOURCE_ROW))
    summary, row_flags = validate(df, RULES)
    return df, summary, row_flags


def test_all_failed_rows_highlights_each_error_rules_columns():
    df, summary, row_flags = _fixture()
    assert failed_cells(df, summary, row_flags, ALL_FAILED, RULES) == {
        (3, "quantity"), (3, "unit_cost"), (3, "unit_price"),  # two rules on one row
        (4, "country"),                                         # only the empty cell
    }


def test_single_rule_highlights_only_its_columns():
    df, summary, row_flags = _fixture()
    assert failed_cells(df, summary, row_flags, "Cost above price", RULES) == {(3, "unit_cost"), (3, "unit_price")}
    assert failed_cells(df, summary, row_flags, "Invalid dates", RULES) == {(6, "order_date")}
    assert failed_cells(df, summary, row_flags, "Duplicate rows", RULES) == set()


def test_failed_cells_limited_to_given_rows():
    df, summary, row_flags = _fixture()
    assert failed_cells(df, summary, row_flags, ALL_FAILED, RULES, index=[4]) == {(4, "country")}


# ---------------------------------------------------------------------- whole numbers
def test_float_column_of_whole_numbers_is_shown_as_integers():
    df = pd.DataFrame({"quantity": [6.0, None, 2.0]})
    assert whole_number_columns(df) == {"quantity"}
    assert format_whole(6.0) == "6"


def test_real_decimals_keep_their_column_as_float():
    df = pd.DataFrame({"price": [6.0, 4.5, None], "label": ["a", "b", "c"], "count": [1, 2, 3]})
    assert whole_number_columns(df) == set()  # 6.0 stays "6.0"; text and int columns are left alone


def test_all_missing_or_infinite_columns_are_not_whole():
    df = pd.DataFrame({"empty": [float("nan")] * 2, "inf": [1.0, float("inf")]})
    assert whole_number_columns(df) == set()


def test_table_model_shows_whole_numbers_and_empty_nan():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from app.ui.data_table import FAILED_CELL_COLOUR, DataFrameModel

    app = QApplication.instance() or QApplication([])
    df = pd.DataFrame({"quantity": [6.0, None], "price": [6.0, 4.5]})
    model = DataFrameModel()
    model.set_frame(df, highlight={(1, "price")}, whole_number_columns=whole_number_columns(df))
    shown = [[model.data(model.index(r, c)) for c in range(2)] for r in range(2)]
    assert shown == [["6", "6.0"], ["", "4.5"]]
    assert model.data(model.index(1, 1), Qt.BackgroundRole).color() == FAILED_CELL_COLOUR
    assert model.data(model.index(0, 1), Qt.BackgroundRole) is None
    assert df["quantity"].dtype == float  # the data itself is unchanged
    app.processEvents()
