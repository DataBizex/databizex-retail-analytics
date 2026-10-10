import pandas as pd

from app.data.cleaner import (
    STATUS_ISSUE,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    change_summary,
    count_exact_duplicates,
    remove_exact_duplicates,
    run_quality_checks,
)
from app.data.loader import load_file
from app.data.profiler import numeric_summary, profile_columns


def _check(report: pd.DataFrame, name: str) -> pd.Series:
    return report.set_index("check").loc[name]


def test_missing_value_profile(quality_df):
    profile = profile_columns(quality_df).set_index("column")
    assert profile.loc["order_date", "missing"] == 1
    assert profile.loc["quantity", "missing"] == 1
    assert profile.loc["country", "missing"] == 0
    assert profile.loc["order_date", "missing_pct"] == 20.0
    assert profile.loc["country", "unique"] == 3
    assert profile["missing"].sum() == 2


def test_numeric_summary_only_numeric_columns(quality_df):
    stats = numeric_summary(quality_df)
    assert "unit_price" in set(stats["column"])
    assert "quantity" not in set(stats["column"])  # contains text 'abc'


def test_quality_checks_counts(quality_df):
    report = run_quality_checks(quality_df)
    assert _check(report, "Missing values (all columns)")["count"] == 2
    assert _check(report, "Exact duplicate rows")["count"] == 2
    assert _check(report, "Invalid dates: order_date")["count"] == 1
    assert _check(report, "Invalid numeric: quantity")["count"] == 1
    assert _check(report, "Invalid numeric: unit_price")["count"] == 0
    assert _check(report, "discount_pct outside 0-100")["count"] == 1
    assert _check(report, "Negative values: unit_cost")["count"] == 1
    assert _check(report, "Negative values: quantity")["count"] == 0
    assert _check(report, "Exact duplicate rows")["status"] == STATUS_ISSUE
    assert _check(report, "Invalid numeric: unit_price")["status"] == STATUS_OK


def test_check_unavailable_when_column_missing():
    report = run_quality_checks(pd.DataFrame({"x": [1, 2]}))
    row = _check(report, "Invalid dates: order_date")
    assert row["status"] == STATUS_UNAVAILABLE
    assert pd.isna(row["count"])


def test_duplicate_detection_and_removal(quality_df):
    assert count_exact_duplicates(quality_df) == 2
    cleaned, removed = remove_exact_duplicates(quality_df)
    assert removed == 2
    assert len(cleaned) == 3
    assert count_exact_duplicates(cleaned) == 0
    # original frame is untouched and missing values are kept
    assert len(quality_df) == 5
    assert cleaned.isna().sum().sum() == 2


def test_duplicate_removal_leaves_source_file_untouched(quality_csv):
    before = quality_csv.read_bytes()
    df = load_file(quality_csv).df
    remove_exact_duplicates(df)
    assert quality_csv.read_bytes() == before


def test_change_summary(quality_df):
    cleaned, _ = remove_exact_duplicates(quality_df)
    summary = change_summary(quality_df, cleaned).set_index("metric")
    assert summary.loc["Rows", "original"] == 5
    assert summary.loc["Rows", "working"] == 3
    assert summary.loc["Rows", "change"] == -2
    assert summary.loc["Exact duplicate rows", "working"] == 0
    assert summary.loc["Missing values (all columns)", "change"] == 0


def test_demo_sample_documented_issue_counts():
    from app.paths import sample_data_path

    report = run_quality_checks(load_file(sample_data_path()).df)
    assert _check(report, "Exact duplicate rows")["count"] == 25
    assert _check(report, "Missing values (all columns)")["count"] == 18
    assert _check(report, "Invalid dates: order_date")["count"] == 8
    invalid_numeric = sum(_check(report, f"Invalid numeric: {c}")["count"]
                          for c in ["quantity", "unit_price", "discount_pct", "unit_cost"])
    assert invalid_numeric == 6
    assert _check(report, "discount_pct outside 0-100")["count"] == 5
    negatives = sum(_check(report, f"Negative values: {c}")["count"]
                    for c in ["quantity", "unit_price", "unit_cost"])
    assert negatives == 6
