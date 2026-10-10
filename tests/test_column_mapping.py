import math

import pandas as pd
import pytest

from app.analytics.kpis import KpiInputError, format_margin, headline_kpis, prepare_sales_frame
from app.data.cleaner import remove_exact_duplicates
from app.data.column_mapping import (
    DISCOUNT_FILLED_NOTE,
    ColumnMappingError,
    apply_column_mapping,
    guess_mapping,
    needs_mapping,
)
from app.data.loader import load_file
from app.database.duckdb_manager import DuckDBManager
from app.paths import sample_data_path

# Consumer-electronics survey export: no order_date, discount or cost columns.
#   net = PurchaseFrequency * ProductPrice (discount filled with 0)
#   = 2*100 + 1*50 + 3*10 = 280, units 6
ELECTRONICS_CSV = """ProductID,ProductCategory,ProductBrand,ProductPrice,CustomerAge,CustomerGender,PurchaseFrequency,CustomerSatisfaction,PurchaseIntent
P1,Smartphones,Apple,100,30,1,2,4,1
P2,Laptops,Dell,50,45,0,1,3,0
P3,Headphones,Sony,10,22,1,3,5,1
"""
ELECTRONICS_MAPPING = {
    "order_id": "ProductID", "order_date": None, "country": None, "product": "ProductBrand",
    "category": "ProductCategory", "quantity": "PurchaseFrequency", "unit_price": "ProductPrice",
    "discount_pct": None, "unit_cost": None,
}


@pytest.fixture
def electronics_df(tmp_path):
    path = tmp_path / "electronics.csv"
    path.write_text(ELECTRONICS_CSV, encoding="utf-8")
    return load_file(path).df


def test_guess_mapping_for_electronics_headers(electronics_df):
    assert needs_mapping(electronics_df)
    assert guess_mapping(list(electronics_df.columns)) == ELECTRONICS_MAPPING


def test_mapped_electronics_file_gives_revenue_and_na_profit(electronics_df):
    before = electronics_df.copy()
    mapped = apply_column_mapping(electronics_df, ELECTRONICS_MAPPING)
    pd.testing.assert_frame_equal(electronics_df, before)  # original untouched
    assert {"CustomerAge", "PurchaseIntent"} <= set(mapped.df.columns)  # unmapped columns kept
    assert (mapped.df["discount_pct"] == 0).all()
    assert sorted(mapped.unavailable) == ["country", "order_date", "unit_cost"]
    assert DISCOUNT_FILLED_NOTE in mapped.notes

    kpis = headline_kpis(prepare_sales_frame(mapped.df, mapped.unavailable))
    assert kpis["net_revenue"] > 0
    assert kpis["net_revenue"] == pytest.approx(280.0)
    assert kpis["units_sold"] == pytest.approx(6.0)
    assert math.isnan(kpis["gross_profit"])
    assert format_margin(kpis["gross_margin"]) == "n/a"

    manager = DuckDBManager()
    manager.load_sales(mapped.df, mapped.unavailable)
    sql = manager.headline()
    by_month = manager.run("by_month")
    manager.close()
    assert sql["net_revenue"] == pytest.approx(280.0)
    assert sql["orders"] == 3
    assert math.isnan(sql["gross_profit"])
    assert math.isnan(sql["gross_margin"])
    assert by_month.empty  # monthly trend n/a


def test_unmapped_quantity_keeps_kpis_unavailable(electronics_df):
    mapping = {**ELECTRONICS_MAPPING, "quantity": None}
    mapped = apply_column_mapping(electronics_df, mapping)
    with pytest.raises(KpiInputError, match="quantity"):
        prepare_sales_frame(mapped.df, mapped.unavailable)


def test_same_source_column_twice_is_rejected(electronics_df):
    mapping = {**ELECTRONICS_MAPPING, "unit_cost": "ProductPrice"}
    with pytest.raises(ColumnMappingError, match="ProductPrice"):
        apply_column_mapping(electronics_df, mapping)


def test_demo_file_needs_no_mapping_and_revenue_unchanged():
    df = load_file(sample_data_path()).df
    assert not needs_mapping(df)
    mapping = guess_mapping(list(df.columns))
    assert all(mapping[c] == c for c in mapping)  # identity: every column found by name
    mapped = apply_column_mapping(df, mapping)
    assert mapped.unavailable == [] and mapped.notes == []
    pd.testing.assert_frame_equal(mapped.df, df)

    cleaned, _ = remove_exact_duplicates(mapped.df)
    manager = DuckDBManager()
    manager.load_sales(cleaned)
    net = manager.headline()["net_revenue"]
    manager.close()
    assert round(net, 2) == 405163.05
