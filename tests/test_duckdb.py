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
