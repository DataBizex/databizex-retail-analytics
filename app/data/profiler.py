"""Column profiling: dtypes, missing values, unique counts, numeric statistics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.cleaner import parse_numeric

# A column gets numeric statistics when at least this share of its non-empty values are numbers.
NUMERIC_SHARE = 0.8
STATS_COLUMNS = ["column", "count", "mean", "std", "min", "25%", "50%", "75%", "max", "non_numeric", "note"]


def profile_columns(df: pd.DataFrame) -> pd.DataFrame:
    """One row per column: name, inferred dtype, non-missing, missing, missing %, unique."""
    rows = len(df)
    missing = df.isna().sum()
    return pd.DataFrame(
        {
            "column": df.columns,
            "dtype": [str(t) for t in df.dtypes],
            "non_missing": (rows - missing).to_numpy(),
            "missing": missing.to_numpy(),
            "missing_pct": np.round(missing.to_numpy() / rows * 100, 2) if rows else 0.0,
            "unique": df.nunique(dropna=True).to_numpy(),
        }
    )


def numeric_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Descriptive statistics for columns that are mostly numbers.

    A column is included when at least NUMERIC_SHARE of its non-empty values parse
    with cleaner.parse_numeric, the parser the quality checks use, so both views
    agree. Statistics cover the values that parse; the rest are counted in
    non_numeric and left as they are in the data.
    """
    rows = []
    for col in df.columns:
        raw = df[col]
        if pd.api.types.is_bool_dtype(raw):
            continue
        present = raw.notna() & (raw.astype("string").str.strip() != "")
        n_present = int(present.sum())
        numbers = parse_numeric(raw)[present].dropna()
        if n_present == 0 or len(numbers) / n_present < NUMERIC_SHARE:
            continue
        stats = numbers.describe().round(2).to_dict()
        stats["count"] = int(stats["count"])
        ignored = n_present - len(numbers)
        note = "" if ignored == 0 else f"{ignored} value{'s' if ignored != 1 else ''} ignored"
        rows.append({"column": col, **stats, "non_numeric": ignored, "note": note})
    return pd.DataFrame(rows, columns=STATS_COLUMNS)


def dataset_overview(df: pd.DataFrame) -> dict[str, int]:
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "missing_cells": int(df.isna().sum().sum()),
    }
