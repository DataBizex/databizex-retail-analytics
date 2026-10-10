import pandas as pd
import pytest

from app.data.cleaner import remove_exact_duplicates
from app.exports.exporter import (
    ExportError,
    export_csv,
    export_excel,
    export_tables_csv,
    generate_powerbi_package,
)


def test_export_cleaned_csv(tmp_path, quality_df):
    cleaned, _ = remove_exact_duplicates(quality_df)
    path = export_csv(cleaned, tmp_path / "clean.csv")
    reloaded = pd.read_csv(path, encoding="utf-8-sig")
    assert len(reloaded) == 3
    assert list(reloaded.columns) == list(quality_df.columns)


def test_export_excel(tmp_path, kpi_df):
    path = export_excel(kpi_df, tmp_path / "out.xlsx", "working_data")
    reloaded = pd.read_excel(path, sheet_name="working_data")
    assert reloaded.shape == (5, 9)


def test_export_excel_adds_extension(tmp_path, kpi_df):
    assert export_excel(kpi_df, tmp_path / "noext").suffix == ".xlsx"


def test_export_to_missing_folder_raises(tmp_path, kpi_df):
    with pytest.raises(ExportError):
        export_csv(kpi_df, tmp_path / "missing" / "x.csv")


def test_export_tables(tmp_path):
    written = export_tables_csv({"a": pd.DataFrame({"x": [1]}), "b": pd.DataFrame({"y": [2]})}, tmp_path)
    assert sorted(p.name for p in written) == ["a.csv", "b.csv"]


def test_powerbi_package(tmp_path, kpi_df):
    written = generate_powerbi_package(kpi_df, tmp_path, "kpi.csv", "Original imported data")
    names = {p.name for p in written}
    assert names == {"sales_clean.csv", "POWER_BI_GUIDE.md", "data_dictionary.md", "package_info.txt"}
    sales = pd.read_csv(tmp_path / "sales_clean.csv", encoding="utf-8-sig")
    assert len(sales) == 4  # A4 excluded
    assert sales["net_revenue"].sum() == pytest.approx(360.0)
    assert sales["gross_profit"].sum() == pytest.approx(168.0)
    assert sales["order_date"].isna().sum() == 1  # A5 kept with blank date
    info = (tmp_path / "package_info.txt").read_text(encoding="utf-8")
    assert "Rows excluded" in info and ": 1" in info
    assert not list(tmp_path.glob("*.pbix"))
