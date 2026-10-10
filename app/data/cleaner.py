"""Data quality checks and the (non-destructive) duplicate-removal step.

Rules followed here:
- Checks only count problems; they never change data.
- The only cleaning action is removing exact duplicate rows, and it returns a
  new DataFrame. Rows with missing values are never deleted or filled.
"""

from __future__ import annotations

import pandas as pd

DATE_COLUMN = "order_date"
NUMERIC_COLUMNS = ["quantity", "unit_price", "discount_pct", "unit_cost"]
NON_NEGATIVE_COLUMNS = ["quantity", "unit_price", "unit_cost"]
DISCOUNT_COLUMN = "discount_pct"

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
    """Return a NEW DataFrame without exact duplicates (first occurrence kept)."""
    mask = df.duplicated(keep="first")
    return df.loc[~mask].reset_index(drop=True).copy(), int(mask.sum())


def count_invalid_dates(df: pd.DataFrame) -> int | None:
    if DATE_COLUMN not in df.columns:
        return None
    raw = df[DATE_COLUMN]
    return int((raw.notna() & parse_dates(raw).isna()).sum())


def count_invalid_numeric(df: pd.DataFrame, column: str) -> int | None:
    """Non-missing values that cannot be read as a number."""
    if column not in df.columns:
        return None
    raw = df[column]
    return int((raw.notna() & parse_numeric(raw).isna()).sum())


def count_discount_out_of_range(df: pd.DataFrame) -> int | None:
    if DISCOUNT_COLUMN not in df.columns:
        return None
    values = parse_numeric(df[DISCOUNT_COLUMN])
    return int(((values < 0) | (values > 100)).sum())


def count_negative(df: pd.DataFrame, column: str) -> int | None:
    if column not in df.columns:
        return None
    return int((parse_numeric(df[column]) < 0).sum())


def _row(check: str, count: int | None, details: str = "") -> dict:
    if count is None:
        return {"check": check, "count": None, "status": STATUS_UNAVAILABLE,
                "details": details or "Required column not found"}
    return {"check": check, "count": count, "status": STATUS_OK if count == 0 else STATUS_ISSUE,
            "details": details}


def run_quality_checks(df: pd.DataFrame) -> pd.DataFrame:
    """Quality summary table with columns: check, count, status, details."""
    rows = []
    missing = df.isna().sum()
    rows.append(_row("Missing values (all columns)", int(missing.sum()), "Total empty cells"))
    for col in df.columns:
        rows.append(_row(f"Missing values: {col}", int(missing[col])))
    rows.append(_row("Exact duplicate rows", count_exact_duplicates(df),
                     "Rows identical to an earlier row in every column"))
    rows.append(_row(f"Invalid dates: {DATE_COLUMN}", count_invalid_dates(df),
                     "Non-empty values that are not a valid YYYY-MM-DD date"
                     if DATE_COLUMN in df.columns else ""))
    for col in NUMERIC_COLUMNS:
        rows.append(_row(f"Invalid numeric: {col}", count_invalid_numeric(df, col),
                         "Non-empty values that are not numbers" if col in df.columns else ""))
    rows.append(_row(f"{DISCOUNT_COLUMN} outside 0-100", count_discount_out_of_range(df)))
    for col in NON_NEGATIVE_COLUMNS:
        rows.append(_row(f"Negative values: {col}", count_negative(df, col)))
    result = pd.DataFrame(rows, columns=["check", "count", "status", "details"])
    result["count"] = result["count"].astype("Int64")
    return result


def change_summary(original: pd.DataFrame, working: pd.DataFrame) -> pd.DataFrame:
    """Before/after comparison between imported data and the cleaned working copy."""
    before = run_quality_checks(original).set_index("check")["count"]
    after = run_quality_checks(working).set_index("check")["count"]
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
