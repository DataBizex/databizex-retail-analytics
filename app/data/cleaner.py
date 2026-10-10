"""Data quality checks and the (non-destructive) duplicate-removal step.

The checks themselves are rules (app.data.rules); without a rule set the
shipped default rules/retail_default.json is used.

Rules followed here:
- Checks only count problems; they never change data.
- The only cleaning action is removing exact duplicate rows, and it returns a
  new DataFrame. Rows with missing values are never deleted or filled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from app.data.rules import Rule

DATE_COLUMN = "order_date"
NUMERIC_COLUMNS = ["quantity", "unit_price", "discount_pct", "unit_cost"]

STATUS_OK = "OK"
STATUS_ISSUE = "Issue"
STATUS_UNAVAILABLE = "Unavailable"


def parse_dates(series: pd.Series) -> pd.Series:
    """Strict ISO (YYYY-MM-DD) parsing; anything else becomes NaT.

    Values that are already datetimes (e.g. from Excel) pass through unchanged.
    """
    return pd.to_datetime(series, errors="coerce", format="ISO8601")


def parse_numeric(series: pd.Series) -> pd.Series:
    """Convert to numbers; text that is not a number becomes NaN."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    return pd.to_numeric(series.astype("string").str.strip(), errors="coerce").astype(float)


def count_exact_duplicates(df: pd.DataFrame) -> int:
    """Rows that are identical to an earlier row in every column."""
    return int(df.duplicated(keep="first").sum())


def remove_exact_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Return a NEW DataFrame without exact duplicates (first occurrence kept).

    The index is kept, so each remaining row keeps its source row number.
    """
    mask = df.duplicated(keep="first")
    return df.loc[~mask].copy(), int(mask.sum())


def run_quality_checks(df: pd.DataFrame, rules: list[Rule] | None = None) -> pd.DataFrame:
    """Quality summary table with columns: check, count, status, severity, details."""
    # Imported here: app.data.rules builds on this module's parsers and status values.
    from app.data.rules import default_rules, validate

    summary, _flags = validate(df, default_rules() if rules is None else rules)
    return summary


def change_summary(original: pd.DataFrame, working: pd.DataFrame,
                   rules: list[Rule] | None = None) -> pd.DataFrame:
    """Before/after comparison between imported data and the cleaned working copy."""
    before = run_quality_checks(original, rules).set_index("check")["count"]
    after = run_quality_checks(working, rules).set_index("check")["count"]
    metrics = [
        ("Rows", len(original), len(working)),
        ("Columns", original.shape[1], working.shape[1]),
    ]
    for check in before.index:
        b = before[check]
        a = after.get(check, pd.NA)
        if pd.isna(b) and pd.isna(a):
            continue
        metrics.append((check, b, a))
    out = pd.DataFrame(metrics, columns=["metric", "original", "working"])
    out["original"] = out["original"].astype("Int64")
    out["working"] = out["working"].astype("Int64")
    out["change"] = out["working"] - out["original"]
    return out
