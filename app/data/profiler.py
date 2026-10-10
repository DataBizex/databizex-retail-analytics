"""Column profiling: dtypes, missing values, unique counts, numeric statistics."""

from __future__ import annotations

import numpy as np
import pandas as pd


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
    """Descriptive statistics for columns pandas inferred as numeric."""
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return pd.DataFrame(columns=["column", "count", "mean", "std", "min", "25%", "50%", "75%", "max"])
    stats = numeric.describe().T.round(2)
    stats.insert(0, "column", stats.index)
    return stats.reset_index(drop=True)


def dataset_overview(df: pd.DataFrame) -> dict[str, int]:
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "missing_cells": int(df.isna().sum().sum()),
    }
