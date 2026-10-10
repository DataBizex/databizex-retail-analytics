import pandas as pd
import pytest

from app.data.cleaner import remove_exact_duplicates
from app.data.failed_rows import ALL_FAILED, FAILED_RULES, failed_rule_choices, failed_rows
from app.data.formats import ImportOptions, apply_import_options
from app.data.loader import SOURCE_ROW, load_file, with_source_rows
from app.data.rules import Rule, default_rules, validate
from app.database.duckdb_manager import DuckDBManager
from app.exports.exporter import POWERBI_COLUMNS, export_csv, generate_powerbi_package
from app.paths import sample_data_path

# File lines (header = line 1):
#   2  C1 ok
#   3  C2 invalid date (warning) + quantity "abc" (error)  -> fails two rules
#   4  (blank)
#   5  exact duplicate of C1 (error: exact duplicate + duplicate order_id)
#   6-7  C4 product with a quoted line break, discount 150 (error)
#   8  C5 unit_price below unit_cost (warning only)
LINES_CSV = (
    "order_id,order_date,country,product,category,quantity,unit_price,discount_pct,unit_cost\n"
    "C1,2024-01-05,Germany,Chair,Furniture,2,100,10,40\n"
    "C2,2024-13-01,France,Lamp,Lighting,abc,50,0,20\n"
    "\n"
    "C1,2024-01-05,Germany,Chair,Furniture,2,100,10,40\n"
    'C4,2024-02-01,Spain,"Mug\nlarge",Kitchen,3,10,150,4\n'
    "C5,2024-02-02,Italy,Desk,Furniture,1,20,0,30\n"
)
DATA_COLUMNS = ["order_id", "order_date", "country", "product", "category",
                "quantity", "unit_price", "discount_pct", "unit_cost"]


def _working(path) -> pd.DataFrame:
    """Working copy built the way MainWindow._make_working_copy builds it."""
    result = load_file(path)
    return apply_import_options(with_source_rows(result.df, result.source_rows), ImportOptions()).df


@pytest.fixture
def lines_csv(tmp_path):
    path = tmp_path / "lines.csv"
    path.write_bytes(LINES_CSV.encode("utf-8"))  # bytes: keep \n line endings exactly
    return path


@pytest.fixture
def validated(lines_csv):
    working = _working(lines_csv)
    summary, flags = validate(working, default_rules())
    return working, summary, flags


def test_source_row_is_the_file_line_before_and_after_duplicate_removal(lines_csv):
    working = _working(lines_csv)
    assert working.index.name == SOURCE_ROW
    assert list(working.index) == [2, 3, 5, 6, 8]
    assert working.loc[6, "product"].startswith("Mug")
    cleaned, removed = remove_exact_duplicates(working)
    assert removed == 1
    assert list(cleaned.index) == [2, 3, 6, 8]  # line 5 removed, the others keep their numbers
    assert list(load_file(lines_csv).df.index) == [0, 1, 2, 3, 4]  # the original import is unchanged


def test_source_row_for_excel_is_the_sheet_row(tmp_path):
    path = tmp_path / "rows.xlsx"
    pd.DataFrame({"a": [1, None, 3], "b": ["x", None, "z"]}).to_excel(path, index=False)
    assert load_file(path).source_rows == [2, 4]  # sheet row 3 is blank and dropped


def test_flag_counts_equal_summary_counts(validated):
    _, summary, flags = validated
    for check, count in summary.dropna(subset=["count"]).set_index("check")["count"].items():
        assert int(flags[check].sum()) == count, check


def test_flag_counts_equal_summary_counts_on_demo():
    summary, flags = validate(load_file(sample_data_path()).df, default_rules())
    for check, count in summary.dropna(subset=["count"]).set_index("check")["count"].items():
        assert int(flags[check].sum()) == count, check


def test_failed_rules_lists_every_rule_a_row_failed(validated):
    working, summary, flags = validated
    table, total = failed_rows(working, summary, flags, "Invalid dates: order_date")
    assert total == 1
    row = table.iloc[0]
    assert row[SOURCE_ROW] == 3
    assert row[FAILED_RULES] == "Invalid dates: order_date; Invalid numeric: quantity"
    dup = failed_rows(working, summary, flags, "Exact duplicate rows")[0].iloc[0]
    assert (dup[SOURCE_ROW], dup[FAILED_RULES]) == (5, "Exact duplicate rows; Duplicate order_id")


def test_all_failed_rows_covers_error_rules_only(validated):
    working, summary, flags = validated
    table, total = failed_rows(working, summary, flags, ALL_FAILED)
    assert table[SOURCE_ROW].tolist() == [3, 5, 6]  # line 8 fails a warning rule only
    assert total == 3
    warning_only = failed_rows(working, summary, flags, "unit_price below unit_cost")[0]
    assert warning_only[SOURCE_ROW].tolist() == [8]  # but it is listed when selected explicitly
    labels = dict(failed_rule_choices(summary, flags))
    assert labels[ALL_FAILED].endswith("(3)")
    assert labels["unit_price below unit_cost"] == "unit_price below unit_cost (1)"


def test_display_is_capped_but_export_writes_every_row(tmp_path):
    df = pd.DataFrame({"quantity": [-1] * 600 + [1] * 10}, index=pd.Index(range(2, 612), name=SOURCE_ROW))
    summary, flags = validate(df, default_rules())
    shown, total = failed_rows(df, summary, flags, "Negative values: quantity", limit=500)
    assert (len(shown), total) == (500, 600)

    table, _ = failed_rows(df, summary, flags, "Negative values: quantity")
    export_csv(table, tmp_path / "failed.csv")
    reloaded = pd.read_csv(tmp_path / "failed.csv", encoding="utf-8")
    assert len(reloaded) == 600
    assert list(reloaded.columns) == [SOURCE_ROW, "quantity", FAILED_RULES]
    assert reloaded[SOURCE_ROW].tolist() == list(range(2, 602))


def test_export_has_source_row_first_then_data_then_failed_rules(validated, tmp_path):
    working, summary, flags = validated
    table, _ = failed_rows(working, summary, flags, ALL_FAILED)
    export_csv(table, tmp_path / "all.csv")
    reloaded = pd.read_csv(tmp_path / "all.csv", encoding="utf-8")
    assert list(reloaded.columns) == [SOURCE_ROW, *DATA_COLUMNS, FAILED_RULES]
    assert len(reloaded) == 3


def test_cells_versus_rows_label_for_missing_values():
    df = pd.DataFrame({"a": [None, 1], "b": [None, 2]})
    labels = dict(failed_rule_choices(*validate(df, default_rules())))
    assert labels["Missing values (all columns)"] == "Missing values (all columns) (2 cells in 1 row)"


def test_source_row_never_reaches_kpis_or_power_bi(lines_csv, tmp_path):
    working = _working(lines_csv)
    manager = DuckDBManager()
    prepared = manager.load_sales(working)
    results = manager.run_all()
    manager.close()
    assert SOURCE_ROW not in prepared.columns and prepared.index.name != SOURCE_ROW
    assert all(SOURCE_ROW not in r.columns for r in results.values())

    generate_powerbi_package(working, tmp_path)
    sales = pd.read_csv(tmp_path / "sales_clean.csv", encoding="utf-8-sig")
    assert list(sales.columns) == POWERBI_COLUMNS


def test_demo_net_revenue_unchanged_with_source_rows():
    working = _working(sample_data_path())
    cleaned, _ = remove_exact_duplicates(working)
    manager = DuckDBManager()
    manager.load_sales(cleaned)
    net = manager.headline()["net_revenue"]
    manager.close()
    assert round(net, 2) == 405163.05


# ---------------------------------------------------------------------- aggregate vs specific missing values
AGGREGATE = "Missing values (all columns)"


def _missing_frame() -> pd.DataFrame:
    """Line 3: unit_price empty. Line 4: country empty. Line 2: complete."""
    return pd.DataFrame({
        "order_id": ["D1", "D2", "D3"], "order_date": ["2024-01-01"] * 3,
        "country": ["Germany", "France", None], "product": ["Chair"] * 3, "category": ["Furniture"] * 3,
        "quantity": [1, 2, 3], "unit_price": [10.0, None, 30.0], "discount_pct": [0, 0, 0],
        "unit_cost": [5.0, 5.0, 5.0],
    }, index=pd.Index([2, 3, 4], name=SOURCE_ROW))


def test_specific_missing_values_rule_replaces_the_aggregate_in_failed_rules():
    df = _missing_frame()
    rules = default_rules()
    summary, flags = validate(df, rules)
    table, _ = failed_rows(df, summary, flags, AGGREGATE, rules=rules)
    by_row = dict(zip(table[SOURCE_ROW], table[FAILED_RULES]))
    assert by_row[3] == "Missing values: unit_price"
    assert by_row[4] == "Missing values: country"
    # the summary, the dropdown and the matching rows are unchanged
    assert summary.set_index("check").loc[AGGREGATE, "count"] == 2
    assert dict(failed_rule_choices(summary, flags))[AGGREGATE] == f"{AGGREGATE} (2)"
    assert failed_rows(df, summary, flags, AGGREGATE)[0][FAILED_RULES].tolist() == [
        f"{AGGREGATE}; Missing values: unit_price", f"{AGGREGATE}; Missing values: country"]  # without rules


def test_aggregate_kept_when_no_specific_rule_covers_the_empty_cell(tmp_path):
    df = _missing_frame()
    rules = [Rule(AGGREGATE, "*", "not_null", severity="warning"),  # no each_column: no per-column rows
             Rule("Missing values: unit_price", "unit_price", "not_null")]
    summary, flags = validate(df, rules)
    table, total = failed_rows(df, summary, flags, AGGREGATE, rules=rules)
    assert total == 2
    by_row = dict(zip(table[SOURCE_ROW], table[FAILED_RULES]))
    assert by_row[3] == "Missing values: unit_price"
    assert by_row[4] == AGGREGATE  # country has no specific rule: the aggregate is the only reason

    export_csv(table, tmp_path / "missing.csv")
    exported = pd.read_csv(tmp_path / "missing.csv", encoding="utf-8")
    assert dict(zip(exported[SOURCE_ROW], exported[FAILED_RULES])) == {
        3: "Missing values: unit_price", 4: AGGREGATE}
