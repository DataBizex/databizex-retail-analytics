"""Display decisions for table views. No Qt here; the data itself is never changed."""

from __future__ import annotations

import numpy as np
import pandas as pd


def whole_number_columns(df: pd.DataFrame) -> set[str]:
    """Float columns whose non-missing values are all whole numbers.

    pandas stores an integer column with missing values as float ("6.0"); table views
    show these columns' values without the trailing ".0".
    """
    columns: set[str] = set()
    for column in df.columns:
        series = df[column]
        if not pd.api.types.is_float_dtype(series):
            continue
        values = series.dropna().to_numpy(dtype=float)
        if len(values) and np.isfinite(values).all() and (values == np.floor(values)).all():
            columns.add(column)
    return columns


def format_whole(value: float) -> str:
    """A whole-number float as an integer: 6.0 -> "6"."""
    return str(int(value))
