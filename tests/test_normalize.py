import os

import pandas as pd
import pytest

from app.data.cleaner import change_summary, remove_exact_duplicates
from app.data.failed_rows import ALL_FAILED, failed_rule_choices, failed_rows
from app.data.formats import ImportOptions, apply_import_options
from app.data.loader import SOURCE_ROW, load_file, with_source_rows
from app.data.normalize import apply_normalization, find_text_variants, variant_groups
from app.data.rules import default_rules, quality_score, validate
from app.database.duckdb_manager import DuckDBManager
from app.paths import sample_data_path

GERMANY_VARIANTS = [" germany", "GERMANY", "Germany ", "Germany"]

# Shaped like "messy retail orders.csv": 4 real countries written 9 ways, plus categories
# with a case variant. Values are quoted so leading spaces survive the CSV reader.
MESSY_COUNTRIES = (["Germany"] * 6 + [" germany"] * 2 + ["GERMANY"] * 2 + ["Germany "] * 2
                   + ["France"] * 4 + ["france"] * 2 + ["Spain"] * 3 + [" SPAIN"] + ["Italy"] * 2)
REAL_COUNTRIES = 4


def _messy_csv(tmp_path):
    header = "order_id,order_date,country,product,category,quantity,unit_price,discount_pct,unit_cost"
    lines = [header]
    for i, country in enumerate(MESSY_COUNTRIES):
        category = ["Furniture", "furniture", "Lighting"][i % 3]
        lines.append(f'M{i:03d},2024-01-{i % 28 + 1:02d},"{country}",Chair,{category},1,{10 * (i + 1)},0,5')
    path = tmp_path / "messy retail orders.csv"
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


def _working(path) -> pd.DataFrame:
    result = load_file(path)
    return apply_import_options(with_source_rows(result.df, result.source_rows), ImportOptions()).df


def test_case_and_spacing_variants_merge_into_one_group():
    groups = variant_groups(pd.Series(GERMANY_VARIANTS + ["Germany", "France"]))
    assert len(groups) == 1
    group = groups[0]
    assert group.canonical == "Germany"  # most frequent (2 of 5)
    assert sorted(v for v, _ in group.variants) == sorted(GERMANY_VARIANTS)
    assert group.changes == 3


def test_internal_whitespace_is_collapsed():
    group = variant_groups(pd.Series(["New York", "New York", "new  york"]))[0]
    assert group.canonical == "New York" and group.changes == 1


def test_frequency_ties_break_alphabetically_after_trimming():
    assert variant_groups(pd.Series(["Paris ", "Paris ", " paris", " paris", "PARIS", "PARIS"]))[0].canonical == "PARIS"
    assert variant_groups(pd.Series(["Paris", "PARIS", "paris"]))[0].canonical == "PARIS"  # all 1x
    assert variant_groups(pd.Series(["rome", "Rome "]))[0].canonical == "Rome"  # "Rome" < "rome" once trimmed


def test_canonical_is_trimmed_even_when_the_most_frequent_variant_has_spaces():
    group = variant_groups(pd.Series([" germany"] * 3 + ["GERMANY"]))[0]
    assert group.canonical == "germany"  # case of " germany", spaces removed
    assert group.changes == 4            # every row changes, " germany" included
    out = apply_normalization(pd.DataFrame({"country": [" germany"] * 12 + ["GERMANY"] * 4 + ["France"] * 4}),
                              ["country"])
    assert set(out["country"]) == {"germany", "France"}


def test_whitespace_only_variants_with_equal_counts_resolve_alphabetically():
    group = variant_groups(pd.Series(["Germany ", "Germany ", " Germany", " Germany"]))[0]
    assert group.canonical == "Germany"
    assert variant_groups(pd.Series([" paris", " paris", "Paris ", "Paris "]))[0].canonical == "Paris"


def test_lone_padded_value_is_reported_and_normalized():
    df = pd.DataFrame({"country": ["Spain "] * 3 + ["France"] * 9})
    groups = find_text_variants(df)["country"]
    assert [(g.canonical, g.variants, g.whitespace_only) for g in groups] == [("Spain", [("Spain ", 3)], True)]
    assert validate(df, default_rules())[0].set_index("check").loc["Text variants: country", "count"] == 3
    assert apply_normalization(df, ["country"])["country"].tolist() == ["Spain"] * 3 + ["France"] * 9


def test_lone_clean_value_is_not_touched():
    df = pd.DataFrame({"country": ["spain"] * 3 + ["France"] * 9})  # odd case, but no second spelling
    assert find_text_variants(df) == {}
    pd.testing.assert_frame_equal(apply_normalization(df, ["country"]), df)


def test_lone_padded_value_and_variant_group_in_one_column(tmp_path):
    df = pd.DataFrame({"country": ["Germany"] * 4 + ["GERMANY"] * 2 + ["Italy  "] * 2 + ["France"] * 8},
                      index=pd.Index(range(2, 18), name=SOURCE_ROW))
    groups = {g.canonical: g for g in find_text_variants(df)["country"]}
    assert set(groups) == {"Germany", "Italy"}
    assert not groups["Germany"].whitespace_only and groups["Italy"].whitespace_only

    summary, flags = validate(df, default_rules())
    assert summary.set_index("check").loc["Text variants: country", "count"] == 4  # 2 GERMANY + 2 "Italy  "
    table, total = failed_rows(df, summary, flags, "Text variants: country")
    assert total == 4 and table[SOURCE_ROW].tolist() == [6, 7, 8, 9]

    out = apply_normalization(df, ["country"])
    assert sorted(out["country"].unique()) == ["France", "Germany", "Italy"]
    original = df.reset_index(drop=True)
    change = change_summary(original, out, distinct={"country": "country"}).set_index("metric")
    assert (change.loc["Distinct values: country", "original"],
            change.loc["Distinct values: country", "working"]) == (4, 3)
    assert (change.loc["Text variants: country", "original"],
            change.loc["Text variants: country", "working"]) == (4, 0)


def test_internal_double_spaces_are_collapsed():
    group = variant_groups(pd.Series(["New  York", "New  York", "new york"]))[0]
    assert group.canonical == "New York"
    assert group.changes == 3


def test_identifier_columns_are_never_normalized():
    df = pd.DataFrame({
        "code": [f"A{i}" if i % 2 else f"a{i}" for i in range(20)],   # every value unique
        "order_id": ["X1", "x1"] * 10,                                   # the order ID, by name
        "country": ["Germany"] * 14 + ["GERMANY"] * 6,
    })
    assert list(find_text_variants(df)) == ["country"]
    normalized = apply_normalization(df, ["order_id", "code", "country"])  # even when selected
    assert normalized["order_id"].tolist() == df["order_id"].tolist()
    assert normalized["code"].tolist() == df["code"].tolist()
    assert normalized["country"].tolist() == ["Germany"] * 20


def test_spelling_differences_are_never_merged():
    df = pd.DataFrame({"country": ["Deutschland", "Germany", "germany", "Grmany"] * 5})
    groups = find_text_variants(df)["country"]
    assert [sorted(v for v, _ in g.variants) for g in groups] == [["Germany", "germany"]]
    out = apply_normalization(df, ["country"])
    assert set(out["country"]) == {"Deutschland", "Germany", "Grmany"}


def test_only_selected_columns_change_and_original_is_untouched(tmp_path):
    working = _working(_messy_csv(tmp_path))
    before = working.copy()
    out = apply_normalization(working, ["country"])
    pd.testing.assert_frame_equal(working, before)                  # input untouched
    assert out["category"].tolist() == working["category"].tolist()  # not selected
    assert out["country"].nunique() == REAL_COUNTRIES
    assert out.index.equals(working.index) and out.index.name == SOURCE_ROW  # source_row survives
    assert list(out.index[:3]) == [2, 3, 4]


def test_messy_orders_have_one_country_per_real_country_after_normalization(tmp_path):
    working = _working(_messy_csv(tmp_path))
    variants = find_text_variants(working)
    assert set(variants) == {"country", "category"}
    assert working["country"].nunique() == 9
    normalized = apply_normalization(working, list(variants))
    assert normalized["country"].nunique() == REAL_COUNTRIES
    assert sorted(normalized["country"].unique()) == ["France", "Germany", "Italy", "Spain"]

    manager = DuckDBManager()
    manager.load_sales(normalized)
    by_country = manager.run("by_country")
    manager.close()
    assert len(by_country) == REAL_COUNTRIES


def test_text_variants_check_is_a_warning_listed_in_failed_rows_but_not_scored(tmp_path):
    working = _working(_messy_csv(tmp_path))
    summary, flags = validate(working, default_rules())
    row = summary.set_index("check").loc["Text variants: country"]
    assert row["count"] == 9  # 2 " germany" + 2 "GERMANY" + 2 "Germany " + 2 "france" + 1 " SPAIN"
    assert row["severity"] == "warning"
    assert row["details"] == "Same value written with different case or spacing"

    choices = dict(failed_rule_choices(summary, flags))
    assert choices["Text variants: country"] == "Text variants: country (9)"
    table, total = failed_rows(working, summary, flags, "Text variants: country")
    assert total == 9 and set(table["country"]) == {" germany", "GERMANY", "Germany ", "france", " SPAIN"}
    all_failed = failed_rows(working, summary, flags, ALL_FAILED)[0]
    assert "Text variants" not in " ".join(all_failed["failed_rules"])  # warnings are not in "All failed rows"

    without = [r for r in default_rules() if r.type != "text_variants"]
    assert quality_score(default_rules(), summary, flags) == quality_score(without, *validate(working, without))

    after = validate(apply_normalization(working, ["country"]), default_rules())[0].set_index("check")
    assert after.loc["Text variants: country", "count"] == 0


def test_change_summary_shows_distinct_counts_for_normalized_columns(tmp_path):
    original = load_file(_messy_csv(tmp_path)).df
    normalized = apply_normalization(_working(_messy_csv(tmp_path)), ["country"])
    table = change_summary(original, normalized, distinct={"country": "country"}).set_index("metric")
    assert (table.loc["Distinct values: country", "original"],
            table.loc["Distinct values: country", "working"]) == (9, REAL_COUNTRIES)


def test_demo_has_no_text_variants_and_revenue_unchanged():
    working = _working(sample_data_path())
    assert find_text_variants(working) == {}
    summary = validate(working, default_rules())[0]
    variants = summary[summary["check"].str.startswith("Text variants")]
    assert list(variants["check"]) == ["Text variants: country", "Text variants: product", "Text variants: category"]
    assert (variants["count"] == 0).all()

    cleaned, _ = remove_exact_duplicates(working)
    manager = DuckDBManager()
    manager.load_sales(cleaned)
    net = manager.headline()["net_revenue"]
    manager.close()
    assert round(net, 2) == 405163.05


# ---------------------------------------------------------------------- the window
@pytest.fixture
def window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    win = main_window.MainWindow()
    yield win, main_window
    win.close()
    app.processEvents()


def test_window_normalize_then_reset(window, tmp_path, monkeypatch):
    win, main_window = window
    monkeypatch.setattr(main_window.NormalizeDialog, "exec", lambda self: 1)  # click Apply, all columns ticked
    assert win.load_path(_messy_csv(tmp_path))
    assert win.run_kpis()
    # KPIs already trim spaces, so "Germany " joins "Germany"; case variants still split:
    # Germany, germany, GERMANY, France, france, Spain, SPAIN, Italy
    assert len(win.kpi_results["by_country"]) == 8

    win.normalize_text()
    assert win.normalized_columns == ["country", "category"]
    assert win.working_df["country"].nunique() == REAL_COUNTRIES
    assert len(win.kpi_results["by_country"]) == REAL_COUNTRIES  # KPI screen refreshed
    assert "text normalized in country, category" in win.data_state_text()
    change = win.change_table.model_._df.set_index("metric")
    assert change.loc["Distinct values: country", "working"] == REAL_COUNTRIES
    assert win.reset_btn.isEnabled()

    win.reset_working_copy()
    assert win.normalized_columns == []
    assert win.working_df["country"].nunique() == 9
    assert "normalized" not in win.data_state_text()
    pd.testing.assert_frame_equal(win.working_df.reset_index(drop=True), win.original_df)


def test_window_reports_when_nothing_needs_normalizing(window, monkeypatch):
    win, main_window = window
    shown = []
    monkeypatch.setattr(main_window.QMessageBox, "information", staticmethod(lambda *a: shown.append(a[1])))
    monkeypatch.setattr(main_window.NormalizeDialog, "exec", lambda self: pytest.fail("dialog opened"))
    assert win.load_sample()
    win.normalize_text()
    assert shown == ["Nothing to normalize"]
