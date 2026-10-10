import pandas as pd
import pytest

from app.analytics.kpis import headline_kpis, prepare_sales_frame, validity_summary
from app.data.cleaner import remove_exact_duplicates
from app.data.column_mapping import apply_column_mapping
from app.data.formats import (
    ImportOptions,
    apply_import_options,
    detect_formats,
    import_summary,
    options_problem,
    parse_localized_numbers,
)
from app.data.loader import DataLoadError, load_file
from app.database.duckdb_manager import DuckDBManager
from app.exports.exporter import generate_powerbi_package
from app.paths import sample_data_path

# German webshop export: semicolon, Windows-1252, decimal comma, dot thousands, DD.MM.YYYY.
#   1001  2 x 43,92    10%   -> net  79.056  cost 2 x 20,50 = 41      (2024-04)
#   1002  1 x 1.234,56  0%   -> net 1234.56  cost 800                 (2024-04)
#   1003  3 x 19,90    5,5%  -> net  56.4165 cost 3 x 8,25 = 24.75    (2024-04)
#   1004  4 x 2,50      0%   -> net  10      cost 4                   31.04. is no date: monthly only
#   1005  price "abc"        -> EXCLUDED
#   1006  1 x 199,00    0%   -> net 199      cost 120                 (2024-05)
# Totals: net 1579.0325, units 11, cost 989.75, profit 589.2825
GERMAN_CSV = """Bestellnummer;Bestelldatum;Land;Artikel;Warengruppe;Menge;Einzelpreis;Rabatt;Einkaufspreis
1001;10.04.2024;Deutschland;Bürostuhl;Möbel;2;43,92;10;20,50
1002;11.04.2024;Österreich;Schreibtisch;Möbel;1;1.234,56;0;800,00
1003;12.04.2024;Schweiz;Lampe;Beleuchtung;3;19,90;5,5;8,25
1004;31.04.2024;Deutschland;Kabel;Zubehör;4;2,50;0;1,00
1005;15.05.2024;Deutschland;Maus;Zubehör;2;abc;0;5,00
1006;16.05.2024;Deutschland;Monitor;Zubehör;1;199,00;0;120,00
"""
GERMAN_MAPPING = {
    "order_id": "Bestellnummer", "order_date": "Bestelldatum", "country": "Land", "product": "Artikel",
    "category": "Warengruppe", "quantity": "Menge", "unit_price": "Einzelpreis",
    "discount_pct": "Rabatt", "unit_cost": "Einkaufspreis",
}
GERMAN_OPTIONS = ImportOptions(";", "cp1252", ",", ".", "%d.%m.%Y")


@pytest.fixture
def german_csv(tmp_path):
    path = tmp_path / "webshop_export.csv"
    path.write_bytes(GERMAN_CSV.encode("cp1252"))
    return path


def test_parse_comma_decimal_and_dot_thousands():
    values = parse_localized_numbers(pd.Series(["43,92", "1.234,56", "-7", "1.234.567,8", "abc", "12,34,5"]),
                                     decimal=",", thousands=".")
    assert values.iloc[:4].tolist() == pytest.approx([43.92, 1234.56, -7.0, 1234567.8])
    assert values.iloc[4:].isna().all()


def test_comma_decimal_column_converted_in_working_copy_only():
    df = pd.DataFrame({"price": ["43,92", "1.234,56", "abc"] + ["5,00"] * 7, "zip": ["01067"] * 10})
    detection = detect_formats(df)
    assert detection.options.decimal == "," and detection.options.thousands == "."
    assert detection.decimal_columns == ["price"]

    before = df.copy()
    converted = apply_import_options(df, detection.options)
    pd.testing.assert_frame_equal(df, before)  # original untouched
    assert converted.numeric_columns == ["price"]
    assert converted.df["price"].iloc[0] == pytest.approx(43.92)
    assert converted.df["price"].iloc[1] == pytest.approx(1234.56)
    assert converted.df["price"].iloc[2] == "abc"  # kept as text so quality checks still flag it
    assert converted.df["zip"].tolist() == ["01067"] * 10  # codes without separators are left alone


def test_dd_mm_yyyy_column_recognized_and_converted():
    df = pd.DataFrame({"Bestelldatum": ["10.04.2024", "1.5.2024", "31.12.2023", "31.04.2024", "15.06.2024"]})
    detection = detect_formats(df)
    assert detection.date_columns == {"Bestelldatum": "%d.%m.%Y"}
    assert detection.options.date_format == "%d.%m.%Y"
    assert detection.ambiguous_date_columns == []

    converted = apply_import_options(df, detection.options).df["Bestelldatum"]
    assert converted.tolist() == ["2024-04-10", "2024-05-01", "2023-12-31", "31.04.2024", "2024-06-15"]


def test_ambiguous_slash_dates_are_flagged_and_read_as_dd_mm():
    df = pd.DataFrame({"date": ["01/02/2024", "03/04/2024", "05/06/2024", "11/12/2024"]})
    detection = detect_formats(df)
    assert detection.date_columns == {"date": "%d/%m/%Y"}
    assert detection.ambiguous_date_columns == ["date"]
    assert any("ambiguous" in note for note in detection.notes)
    assert not detection.is_default()

    converted = apply_import_options(df, detection.options)
    assert converted.df["date"].iloc[0] == "2024-02-01"  # 1 February, not 2 January
    assert any("ambiguous" in note for note in converted.notes)


def test_unambiguous_slash_dates_pick_the_right_order():
    assert detect_formats(pd.DataFrame({"d": ["25/12/2024", "01/02/2024"]})).date_columns == {"d": "%d/%m/%Y"}
    mdy = detect_formats(pd.DataFrame({"d": ["12/25/2024", "01/02/2024"]}))
    assert mdy.date_columns == {"d": "%m/%d/%Y"} and not mdy.ambiguous_date_columns


def test_plain_iso_utf8_file_needs_no_import_dialog(kpi_csv):
    result = load_file(kpi_csv)
    detection = detect_formats(result.df, result.delimiter, result.encoding)
    assert (result.delimiter, result.encoding) == (",", "utf-8-sig")
    assert detection.options == ImportOptions()
    assert detection.is_default()  # MainWindow.load_path skips the dialog when this is True


def test_demo_file_is_default_and_revenue_unchanged():
    result = load_file(sample_data_path())
    detection = detect_formats(result.df, result.delimiter, result.encoding)
    assert detection.is_default()
    converted = apply_import_options(result.df, detection.options)
    pd.testing.assert_frame_equal(converted.df, result.df)
    assert converted.numeric_columns == [] and converted.date_columns == []

    cleaned, _ = remove_exact_duplicates(converted.df)
    manager = DuckDBManager()
    manager.load_sales(cleaned)
    net = manager.headline()["net_revenue"]
    manager.close()
    assert round(net, 2) == 405163.05


def test_german_export_detected_end_to_end(german_csv):
    result = load_file(german_csv)
    detection = detect_formats(result.df, result.delimiter, result.encoding)
    assert detection.options == GERMAN_OPTIONS
    assert detection.decimal_columns == ["Einzelpreis", "Rabatt", "Einkaufspreis"]
    assert detection.date_columns == {"Bestelldatum": "%d.%m.%Y"}


def test_german_export_gives_valid_kpis_after_conversion(german_csv, tmp_path):
    original = load_file(german_csv).df
    before = original.copy()
    converted = apply_import_options(original, GERMAN_OPTIONS)
    working = apply_column_mapping(converted.df, GERMAN_MAPPING).df
    pd.testing.assert_frame_equal(original, before)

    prepared = prepare_sales_frame(working)
    kpis = headline_kpis(prepared)
    assert kpis["net_revenue"] == pytest.approx(1579.0325)
    assert kpis["units_sold"] == pytest.approx(11.0)
    assert kpis["gross_profit"] == pytest.approx(589.2825)
    summary = validity_summary(prepared)
    assert (summary.kpi_rows, summary.excluded_rows, summary.kpi_rows_without_valid_date) == (5, 1, 1)

    manager = DuckDBManager()
    manager.load_sales(working)
    sql, by_month = manager.headline(), manager.run("by_month")
    manager.close()
    assert sql["net_revenue"] == pytest.approx(1579.0325)
    assert by_month.set_index("month")["net_revenue"].to_dict() == pytest.approx(
        {"2024-04": 1370.0325, "2024-05": 199.0})

    settings = import_summary(GERMAN_OPTIONS, converted)
    assert "semicolon delimiter" in settings and "decimal comma" in settings and "DD.MM.YYYY" in settings
    assert "converted to numbers: Einzelpreis, Rabatt, Einkaufspreis" in settings
    generate_powerbi_package(working, tmp_path, german_csv.name, "Original imported data", (), settings)
    info = (tmp_path / "package_info.txt").read_text(encoding="utf-8")
    assert f"Import settings: {settings}" in info


def test_overriding_delimiter_and_encoding(german_csv):
    result = load_file(german_csv, delimiter=";", encoding="cp1252")
    assert result.df.loc[0, "Artikel"] == "Bürostuhl"
    assert (result.delimiter, result.encoding) == (";", "cp1252")
    # A wrong override gives a readable error (shown by MainWindow.load_path), not a crash.
    with pytest.raises(DataLoadError):
        load_file(german_csv, delimiter=",", encoding="cp1252")


def test_same_decimal_and_thousands_separator_is_rejected():
    options = ImportOptions(decimal=",", thousands=",")
    assert options_problem(options)
    with pytest.raises(ValueError):
        apply_import_options(pd.DataFrame({"x": ["1,5"]}), options)
