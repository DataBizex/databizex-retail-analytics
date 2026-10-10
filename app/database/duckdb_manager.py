"""Embedded DuckDB database holding the working dataset as table `sales`.

All reporting queries are plain, read-only standard SQL (SELECT, SUM, CASE,
EXTRACT, GROUP BY, ORDER BY, LIMIT) so they also run on MySQL and PostgreSQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb
import pandas as pd

from app.analytics.kpis import UNKNOWN_LABEL, gross_margin, prepare_sales_frame

logger = logging.getLogger(__name__)

TABLE_COLUMNS = [
    "order_id", "order_date", "country", "product", "category",
    "quantity", "unit_price", "discount_pct", "unit_cost", "kpi_valid", "date_valid",
]

NET_REVENUE_SQL = "quantity * unit_price * (1 - discount_pct / 100.0)"
GROSS_PROFIT_SQL = f"({NET_REVENUE_SQL}) - quantity * unit_cost"


@dataclass(frozen=True)
class NamedQuery:
    key: str
    title: str
    sql: str


QUERIES: dict[str, NamedQuery] = {
    q.key: q
    for q in [
        NamedQuery(
            "headline",
            "Headline KPIs",
            f"""SELECT
    COUNT(*)                         AS orders,
    SUM(quantity)                    AS units_sold,
    SUM({NET_REVENUE_SQL})  AS net_revenue,
    SUM({GROSS_PROFIT_SQL}) AS gross_profit
FROM sales
WHERE kpi_valid = 1""",
        ),
        NamedQuery(
            "by_country",
            "Revenue by country",
            f"""SELECT
    COALESCE(country, '{UNKNOWN_LABEL}') AS country,
    SUM({NET_REVENUE_SQL})  AS net_revenue,
    SUM(quantity)                    AS units_sold
FROM sales
WHERE kpi_valid = 1
GROUP BY COALESCE(country, '{UNKNOWN_LABEL}')
ORDER BY net_revenue DESC""",
        ),
        NamedQuery(
            "by_category",
            "Revenue by category",
            f"""SELECT
    COALESCE(category, '{UNKNOWN_LABEL}') AS category,
    SUM({NET_REVENUE_SQL})  AS net_revenue,
    SUM({GROSS_PROFIT_SQL}) AS gross_profit
FROM sales
WHERE kpi_valid = 1
GROUP BY COALESCE(category, '{UNKNOWN_LABEL}')
ORDER BY net_revenue DESC""",
        ),
        NamedQuery(
            "by_month",
            "Revenue by month (valid dates only)",
            f"""SELECT
    EXTRACT(YEAR FROM order_date)  AS order_year,
    EXTRACT(MONTH FROM order_date) AS order_month,
    SUM({NET_REVENUE_SQL})  AS net_revenue
FROM sales
WHERE kpi_valid = 1 AND date_valid = 1
GROUP BY EXTRACT(YEAR FROM order_date), EXTRACT(MONTH FROM order_date)
ORDER BY order_year, order_month""",
        ),
        NamedQuery(
            "top_products",
            "Top 5 products by revenue",
            f"""SELECT
    COALESCE(product, '{UNKNOWN_LABEL}') AS product,
    SUM({NET_REVENUE_SQL})  AS net_revenue,
    SUM(quantity)                    AS units_sold
FROM sales
WHERE kpi_valid = 1
GROUP BY COALESCE(product, '{UNKNOWN_LABEL}')
ORDER BY net_revenue DESC
LIMIT 5""",
        ),
    ]
}


class DuckDBManager:
    """Owns an in-memory DuckDB connection with one table: sales."""

    def __init__(self) -> None:
        self._con = duckdb.connect(database=":memory:")
        self.row_count = 0

    def load_sales(self, df: pd.DataFrame) -> pd.DataFrame:
        """(Re)create table `sales` from the dataset. Returns the prepared frame."""
        prepared = prepare_sales_frame(df)
        table = prepared[TABLE_COLUMNS].copy()
        for col in ["order_id", "country", "product", "category"]:
            table[col] = table[col].astype(object).where(table[col].notna(), None)
        self._con.register("sales_frame", table)
        try:
            self._con.execute("DROP TABLE IF EXISTS sales")
            self._con.execute(
                """CREATE TABLE sales AS SELECT
                    CAST(order_id AS VARCHAR) AS order_id,
                    CAST(order_date AS DATE) AS order_date,
                    CAST(country AS VARCHAR) AS country,
                    CAST(product AS VARCHAR) AS product,
                    CAST(category AS VARCHAR) AS category,
                    CAST(quantity AS DOUBLE) AS quantity,
                    CAST(unit_price AS DOUBLE) AS unit_price,
                    CAST(discount_pct AS DOUBLE) AS discount_pct,
                    CAST(unit_cost AS DOUBLE) AS unit_cost,
                    CAST(kpi_valid AS INTEGER) AS kpi_valid,
                    CAST(date_valid AS INTEGER) AS date_valid
                FROM sales_frame"""
            )
        finally:
            self._con.unregister("sales_frame")
        self.row_count = len(table)
        logger.info("Loaded %d rows into DuckDB table sales", self.row_count)
        return prepared

    def run(self, key: str) -> pd.DataFrame:
        query = QUERIES[key]
        result = self._con.execute(query.sql).df()
        if key == "by_month" and not result.empty:
            result.insert(0, "month", [f"{int(y):04d}-{int(m):02d}"
                                       for y, m in zip(result["order_year"], result["order_month"])])
            result = result.drop(columns=["order_year", "order_month"])
        return result

    def headline(self) -> dict[str, float]:
        row = self.run("headline").iloc[0]
        net = float(row["net_revenue"]) if pd.notna(row["net_revenue"]) else 0.0
        profit = float(row["gross_profit"]) if pd.notna(row["gross_profit"]) else 0.0
        return {
            "orders": int(row["orders"]),
            "units_sold": float(row["units_sold"]) if pd.notna(row["units_sold"]) else 0.0,
            "net_revenue": net,
            "gross_profit": profit,
            "gross_margin": gross_margin(profit, net),
        }

    def run_all(self) -> dict[str, pd.DataFrame]:
        return {key: self.run(key) for key in QUERIES}

    def close(self) -> None:
        self._con.close()
