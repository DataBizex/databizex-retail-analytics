"""Business KPI definitions and the typed, validated frame used for analysis.

Definitions (see README "KPI definitions and assumptions"):
    net revenue   = quantity * unit_price * (1 - discount_pct / 100)
    gross profit  = net revenue - quantity * unit_cost
    gross margin  = gross profit / net revenue   (only when net revenue != 0)
    units sold    = sum of quantity

A row counts towards KPIs only if quantity, unit_price, discount_pct and
unit_cost are all valid numbers, none is negative, and discount_pct is within
0-100. Monthly figures additionally need a valid order_date. Rows that fail are
excluded and reported, never repaired or filled with zero.

When the user's column mapping marks unit_cost as unavailable, rows are valid
without it and gross profit / margin are NaN ("n/a"). order_date and the
dimension columns may also be unavailable; quantity, unit_price and
discount_pct never are.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.data.cleaner import DATE_COLUMN, NUMERIC_COLUMNS, parse_dates, parse_numeric

REQUIRED_COLUMNS = ["order_date", "country", "product", "category", *NUMERIC_COLUMNS]
DIMENSION_COLUMNS = ["order_id", "country", "product", "category"]
# Required columns that a column mapping may mark as unavailable.
OPTIONAL_COLUMNS = ["order_date", "country", "product", "category", "unit_cost"]
UNKNOWN_LABEL = "(missing)"


class KpiInputError(Exception):
    """Raised when the dataset lacks the columns needed for KPIs."""


@dataclass
class ValiditySummary:
    total_rows: int
    kpi_rows: int
    excluded_rows: int
    kpi_rows_without_valid_date: int


def missing_required_columns(df: pd.DataFrame, unavailable: Collection[str] = ()) -> list[str]:
    optional = set(unavailable) & set(OPTIONAL_COLUMNS)
    return [c for c in REQUIRED_COLUMNS if c not in df.columns and c not in optional]


def prepare_sales_frame(df: pd.DataFrame, unavailable: Collection[str] = ()) -> pd.DataFrame:
    """Typed copy of the data with validity flags (kpi_valid, date_valid as 0/1).

    unavailable: columns the user's column mapping marked as not available
    (only OPTIONAL_COLUMNS are honoured). They are treated as empty even if a
    column with that name exists. The input DataFrame is not modified.
    """
    missing = missing_required_columns(df, unavailable)
    if missing:
        raise KpiInputError("KPIs need these columns, which were not found: " + ", ".join(missing))
    skipped = set(unavailable) & set(OPTIONAL_COLUMNS)

    out = pd.DataFrame(index=df.index)
    for col in DIMENSION_COLUMNS:
        if col in df.columns and col not in skipped:
            out[col] = df[col].astype("string").str.strip()
        else:
            out[col] = pd.Series(pd.NA, index=df.index, dtype="string")
    if DATE_COLUMN in skipped:
        out[DATE_COLUMN] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    else:
        out[DATE_COLUMN] = parse_dates(df[DATE_COLUMN]).dt.normalize()
    for col in NUMERIC_COLUMNS:
        out[col] = float("nan") if col in skipped else parse_numeric(df[col])

    checked = [c for c in NUMERIC_COLUMNS if c not in skipped]
    numbers_ok = out[checked].notna().all(axis=1)
    non_negative = (out[[c for c in ["quantity", "unit_price", "unit_cost"] if c in checked]] >= 0).all(axis=1)
    discount_ok = out["discount_pct"].between(0, 100)
    out["kpi_valid"] = (numbers_ok & non_negative & discount_ok).astype(int)
    out["date_valid"] = out[DATE_COLUMN].notna().astype(int)

    net = out["quantity"] * out["unit_price"] * (1 - out["discount_pct"] / 100)
    out["net_revenue"] = net.where(out["kpi_valid"] == 1)
    out["gross_profit"] = (net - out["quantity"] * out["unit_cost"]).where(out["kpi_valid"] == 1)
    return out.reset_index(drop=True)


def validity_summary(prepared: pd.DataFrame) -> ValiditySummary:
    valid = prepared["kpi_valid"] == 1
    return ValiditySummary(
        total_rows=int(len(prepared)),
        kpi_rows=int(valid.sum()),
        excluded_rows=int((~valid).sum()),
        kpi_rows_without_valid_date=int((valid & (prepared["date_valid"] == 0)).sum()),
    )


def gross_margin(gross_profit: float, net_revenue: float) -> float:
    """Gross margin as a fraction; NaN when net revenue is zero."""
    return float(gross_profit / net_revenue) if net_revenue else float("nan")


def headline_kpis(prepared: pd.DataFrame) -> dict[str, float]:
    """Pandas reference implementation (used to cross-check the SQL results)."""
    valid = prepared[prepared["kpi_valid"] == 1]
    net = float(valid["net_revenue"].sum())
    # NaN (n/a) when valid rows exist but none has a cost, i.e. unit_cost unavailable.
    profit = float(valid["gross_profit"].sum(min_count=1)) if len(valid) else 0.0
    return {
        "net_revenue": net,
        "units_sold": float(valid["quantity"].sum()),
        "gross_profit": profit,
        "gross_margin": gross_margin(profit, net),
        "orders": int(len(valid)),
    }


def revenue_by(prepared: pd.DataFrame, column: str) -> pd.Series:
    valid = prepared[prepared["kpi_valid"] == 1]
    keys = valid[column].fillna(UNKNOWN_LABEL)
    return valid.groupby(keys)["net_revenue"].sum().sort_values(ascending=False)


def format_margin(value: float) -> str:
    return "n/a" if value is None or np.isnan(value) else f"{value:.1%}"
