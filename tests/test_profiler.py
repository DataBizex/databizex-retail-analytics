import pandas as pd
import pytest

from app.data.cleaner import run_quality_checks
from app.data.formats import ImportOptions, apply_import_options
from app.data.loader import load_file
from app.data.profiler import numeric_summary
from app.paths import sample_data_path


def _stats(df: pd.DataFrame) -> pd.DataFrame:
    return numeric_summary(df).set_index("column")


def test_mostly_numeric_column_counts_parsed_values_only():
    values = [str(i) for i in range(1, 96)] + ["n/a", "abc", "?", "x", "-"]  # 95 numbers, 5 text
    df = pd.DataFrame({"amount": values})
    before = df.copy()
    row = _stats(df).loc["amount"]
    assert row["count"] == 95
    assert row["non_numeric"] == 5
    assert row["note"] == "5 values ignored"
    assert row["mean"] == pytest.approx(48.0)  # mean of 1..95
    assert (row["min"], row["max"]) == (1.0, 95.0)
    pd.testing.assert_frame_equal(df, before)  # the text values stay in the data


def test_half_text_column_is_excluded():
    df = pd.DataFrame({"mixed": ["1", "2", "3", "a", "b", "c"], "n": [1, 2, 3, 4, 5, 6]})
    assert list(numeric_summary(df)["column"]) == ["n"]


def test_clean_numeric_column_has_no_ignored_values():
    row = _stats(pd.DataFrame({"qty": [1, 2, 3, None]})).loc["qty"]
    assert row["count"] == 3  # empty cells are neither numbers nor ignored values
    assert row["non_numeric"] == 0
    assert row["note"] == ""


def test_demo_lists_all_numeric_columns_and_agrees_with_quality_checks():
    df = load_file(sample_data_path()).df
    stats = _stats(df)
    assert list(stats.index) == ["quantity", "unit_price", "discount_pct", "unit_cost"]
    checks = run_quality_checks(df).set_index("check")["count"]
    for col in stats.index:
        assert stats.loc[col, "non_numeric"] == checks[f"Invalid numeric: {col}"]


def test_statistics_use_import_converted_values():
    df = pd.DataFrame({"Einzelpreis": ["43,92", "1.234,56", "19,90", "2,50", "199,00", "k.A."]})
    assert numeric_summary(df).empty  # as read, "43,92" is text
    converted = apply_import_options(df, ImportOptions(";", "cp1252", ",", ".")).df
    row = _stats(converted).loc["Einzelpreis"]
    assert row["count"] == 5 and row["non_numeric"] == 1
    assert row["max"] == pytest.approx(1234.56)
