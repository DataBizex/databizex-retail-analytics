import math

import pandas as pd
import pytest

from app.analytics.kpis import (
    KpiInputError,
    headline_kpis,
    prepare_sales_frame,
    revenue_by,
    validity_summary,
)


def test_headline_kpis_hand_verified(kpi_df):
    kpis = headline_kpis(prepare_sales_frame(kpi_df))
    assert kpis["net_revenue"] == pytest.approx(360.0)
    assert kpis["units_sold"] == pytest.approx(10.0)
    assert kpis["gross_profit"] == pytest.approx(168.0)
    assert kpis["gross_margin"] == pytest.approx(168 / 360)
    assert kpis["orders"] == 4


def test_invalid_rows_are_excluded_not_zeroed(kpi_df):
    prepared = prepare_sales_frame(kpi_df)
    summary = validity_summary(prepared)
    assert summary.total_rows == 5
    assert summary.kpi_rows == 4
    assert summary.excluded_rows == 1          # A4 negative quantity
    assert summary.kpi_rows_without_valid_date == 1  # A5 invalid date
    assert pd.isna(prepared.loc[3, "net_revenue"])


def test_revenue_by_country(kpi_df):
    by_country = revenue_by(prepare_sales_frame(kpi_df), "country")
    assert by_country.to_dict() == pytest.approx({"Germany": 280.0, "France": 50.0, "Spain": 30.0})


def test_missing_discount_is_not_treated_as_zero():
    df = pd.DataFrame({
        "order_date": ["2024-01-01", "2024-01-02"], "country": ["Italy", "Italy"],
        "product": ["P", "P"], "category": ["C", "C"], "quantity": [1, 1],
        "unit_price": [100, 100], "discount_pct": [None, 0], "unit_cost": [50, 50],
    })
    kpis = headline_kpis(prepare_sales_frame(df))
    assert kpis["net_revenue"] == pytest.approx(100.0)  # only the complete row
    assert kpis["orders"] == 1


def test_zero_revenue_margin_is_nan():
    df = pd.DataFrame({
        "order_date": ["2024-01-01"], "country": ["Italy"], "product": ["P"], "category": ["C"],
        "quantity": [0], "unit_price": [10], "discount_pct": [0], "unit_cost": [5],
    })
    assert math.isnan(headline_kpis(prepare_sales_frame(df))["gross_margin"])


def test_missing_columns_raise():
    with pytest.raises(KpiInputError, match="unit_cost"):
        prepare_sales_frame(pd.DataFrame({"order_date": [], "country": [], "product": [],
                                          "category": [], "quantity": [], "unit_price": [],
                                          "discount_pct": []}))


def test_prepare_does_not_modify_input(kpi_df):
    before = kpi_df.copy()
    prepare_sales_frame(kpi_df)
    pd.testing.assert_frame_equal(kpi_df, before)
