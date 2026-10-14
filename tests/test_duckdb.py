import pandas as pd
import pytest

from app.analytics.kpis import headline_kpis
from app.data.cleaner import remove_exact_duplicates
from app.data.loader import load_file
from app.database.duckdb_manager import QUERIES, DuckDBManager
from app.paths import sample_data_path


@pytest.fixture
def db(kpi_df):
    manager = DuckDBManager()
    manager.load_sales(kpi_df)
    yield manager
    manager.close()


def test_headline_sql(db):
    kpis = db.headline()
    assert kpis["net_revenue"] == pytest.approx(360.0)
    assert kpis["units_sold"] == pytest.approx(10.0)
    assert kpis["gross_profit"] == pytest.approx(168.0)
    assert kpis["gross_margin"] == pytest.approx(0.466667, rel=1e-5)
    assert kpis["orders"] == 4


def test_revenue_by_country_sql(db):
    result = db.run("by_country")
    assert list(result["country"]) == ["Germany", "France", "Spain"]
    assert list(result["net_revenue"]) == pytest.approx([280.0, 50.0, 30.0])


def test_revenue_by_category_sql(db):
    result = db.run("by_category").set_index("category")["net_revenue"]
    assert result.to_dict() == pytest.approx({"Furniture": 180.0, "Lighting": 150.0, "Kitchen": 30.0})


def test_revenue_by_month_uses_valid_dates_only(db):
    result = db.run("by_month")
    assert list(result["month"]) == ["2024-01", "2024-02"]
    assert list(result["net_revenue"]) == pytest.approx([230.0, 100.0])  # A5 (invalid date) left out


def test_month_over_month_change(db):
    result = db.run("by_month")
    assert pd.isna(result["mom_pct"].iloc[0])  # first month has no previous month
    assert result["mom_pct"].iloc[1] == -56.5  # (100 - 230) / 230 = -56.52%


def test_month_over_month_on_demo_data():
    df, _ = remove_exact_duplicates(load_file(sample_data_path()).df)
    manager = DuckDBManager()
    manager.load_sales(df)
    result = manager.run("by_month").set_index("month")
    manager.close()
    assert pd.isna(result.loc["2024-01", "mom_pct"])
    # 2024-01: 14,152.275; 2024-02: 19,133.80 -> 4,981.525 / 14,152.275 = +35.20%
    assert result.loc["2024-01", "net_revenue"] == pytest.approx(14152.275)
    assert result.loc["2024-02", "net_revenue"] == pytest.approx(19133.8)
    assert result.loc["2024-02", "mom_pct"] == 35.2


def test_month_over_month_is_null_after_a_zero_revenue_month():
    df = pd.DataFrame({"order_id": ["a", "b", "c"], "order_date": ["2024-01-05", "2024-02-05", "2024-03-05"],
                       "country": "X", "product": "P", "category": "C", "quantity": [1, 2, 1],
                       "unit_price": [0, 10, 30], "discount_pct": 0, "unit_cost": 0})
    manager = DuckDBManager()
    manager.load_sales(df)
    result = manager.run("by_month")
    manager.close()
    assert list(result["net_revenue"]) == pytest.approx([0.0, 20.0, 30.0])
    assert pd.isna(result["mom_pct"].iloc[0]) and pd.isna(result["mom_pct"].iloc[1])
    assert result["mom_pct"].iloc[2] == 50.0


def test_monthly_table_shows_signed_mom_percent():
    from app.ui.main_window import _display_frame

    frame = pd.DataFrame({"month": ["2024-01", "2024-02", "2024-03"], "net_revenue": [1.0, 2.0, 1.0],
                          "mom_pct": [float("nan"), 12.4, -3.1]})
    shown = _display_frame("by_month", frame)
    assert list(shown.columns) == ["month", "net_revenue", "MoM %"]
    assert list(shown["MoM %"]) == ["n/a", "+12.4%", "-3.1%"]
    assert "mom_pct" in frame.columns  # the stored result keeps the raw numbers


def test_top_products_sql(db):
    result = db.run("top_products")
    assert list(result["product"]) == ["Chair", "Lamp", "Mug"]
    assert list(result["net_revenue"]) == pytest.approx([180.0, 150.0, 30.0])


def test_queries_are_read_only_select_statements():
    for query in QUERIES.values():
        sql = query.sql.upper()
        assert sql.lstrip().startswith("SELECT")
        for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "COPY", "ATTACH"):
            assert word not in sql


def test_sql_matches_pandas_on_sample_data():
    df, _ = remove_exact_duplicates(load_file(sample_data_path()).df)
    manager = DuckDBManager()
    prepared = manager.load_sales(df)
    sql, ref = manager.headline(), headline_kpis(prepared)
    manager.close()
    for key in ("net_revenue", "units_sold", "gross_profit", "gross_margin", "orders"):
        assert sql[key] == pytest.approx(ref[key])
    assert ref["orders"] == 1475  # 1500 unique rows - 25 rows with invalid numerics


def _run(rows: list[tuple[str, str, float, float]], key: str) -> pd.DataFrame:
    """Run one query on rows of (order_date, product, quantity, unit_price); no discount or cost."""
    df = pd.DataFrame(rows, columns=["order_date", "product", "quantity", "unit_price"])
    df = df.assign(order_id=[f"R{i}" for i in range(len(df))], country="X", category="C",
                   discount_pct=0, unit_cost=0)
    manager = DuckDBManager()
    manager.load_sales(df)
    result = manager.run(key)
    manager.close()
    return result


# ---------------------------------------------------------------------- year over year
def test_year_over_year_against_same_month_a_year_earlier():
    # 2024-04 and most 2024 months are missing, so LAG(12) would compare the wrong months.
    result = _run([("2024-03-10", "A", 1, 100), ("2024-06-10", "A", 1, 0),
                   ("2025-03-10", "A", 1, 125), ("2025-04-10", "A", 1, 80), ("2025-06-10", "A", 1, 50)],
                  "by_month").set_index("month")
    assert result.loc["2025-03", "yoy_pct"] == 25.0  # (125 - 100) / 100
    assert pd.isna(result.loc["2025-04", "yoy_pct"])  # no 2024-04
    assert pd.isna(result.loc["2025-06", "yoy_pct"])  # 2024-06 revenue was 0
    assert pd.isna(result.loc["2024-03", "yoy_pct"])
    assert result.loc["2025-04", "mom_pct"] == -36.0  # MoM is unchanged: (80 - 125) / 125


def test_year_over_year_on_demo_data():
    df, _ = remove_exact_duplicates(load_file(sample_data_path()).df)
    manager = DuckDBManager()
    manager.load_sales(df)
    result = manager.run("by_month").set_index("month")
    manager.close()
    assert result.loc["2024-01":"2024-12", "yoy_pct"].isna().all()
    assert result.loc["2025-01":"2025-12", "yoy_pct"].notna().all()
    # 2024-01: 14,152.275; 2025-01: 11,416.70 -> -2,735.575 / 14,152.275 = -19.33%
    assert result.loc["2025-01", "net_revenue"] == pytest.approx(11416.7)
    assert result.loc["2025-01", "yoy_pct"] == -19.3


# ---------------------------------------------------------------------- Pareto
def test_pareto_shares_and_cumulative_order():
    # Revenue A 50, B 25, C 20, D 5 (total 100): C crosses 80% (75 -> 95) and is the last included.
    result = _run([("2024-01-01", "C", 2, 10), ("2024-01-01", "A", 1, 50), ("2024-01-01", "D", 1, 5),
                   ("2024-01-01", "B", 1, 25)], "pareto")
    assert list(result["product"]) == ["A", "B", "C", "D"]
    assert list(result["net_revenue"]) == pytest.approx([50, 25, 20, 5])
    assert list(result["share_pct"]) == [50.0, 25.0, 20.0, 5.0]
    assert list(result["cum_share_pct"]) == [50.0, 75.0, 95.0, 100.0]
    assert list(result["in_top_80"]) == [1, 1, 1, 0]


def test_pareto_product_reaching_exactly_80_percent_is_the_last_included():
    # A 50, B 30 -> exactly 80%; C is not needed. Ties (C, D at 10) are ordered by name.
    result = _run([("2024-01-01", "A", 1, 50), ("2024-01-01", "B", 1, 30), ("2024-01-01", "D", 1, 10),
                   ("2024-01-01", "C", 1, 10)], "pareto")
    assert list(result["product"]) == ["A", "B", "C", "D"]
    assert list(result["cum_share_pct"]) == [50.0, 80.0, 90.0, 100.0]
    assert list(result["in_top_80"]) == [1, 1, 0, 0]


def test_pareto_on_demo_data():
    from app.ui.main_window import pareto_summary

    df, _ = remove_exact_duplicates(load_file(sample_data_path()).df)
    manager = DuckDBManager()
    manager.load_sales(df)
    result = manager.run("pareto")
    manager.close()
    assert result["product"].iloc[0] == "Two-Seat Sofa"
    assert result["cum_share_pct"].iloc[-1] == 100.0
    assert pareto_summary(result) == "14 of 26 products make 80% of net revenue."


def test_pareto_and_yoy_table_formatting():
    from app.ui.main_window import _display_frame

    pareto = pd.DataFrame({"product": ["A", "B"], "net_revenue": [80.0, 20.0], "share_pct": [80.0, 20.0],
                           "cum_share_pct": [80.0, 100.0], "in_top_80": [1, 0]})
    shown = _display_frame("pareto", pareto)
    assert list(shown.columns) == ["product", "net_revenue", "Share %", "Cumulative %", "Top 80%"]
    assert list(shown["Share %"]) == ["80.0%", "20.0%"] and list(shown["Top 80%"]) == ["Yes", ""]
    month = pd.DataFrame({"month": ["2025-01"], "net_revenue": [1.0], "mom_pct": [5.0], "yoy_pct": [float("nan")]})
    assert list(_display_frame("by_month", month).columns) == ["month", "net_revenue", "MoM %", "YoY %"]
    assert _display_frame("by_month", month)["YoY %"].iloc[0] == "n/a"
    assert pareto["in_top_80"].tolist() == [1, 0]  # stored results keep raw numbers
