"""The messy multi-table sample: generator determinism, keys, the bundled files and the rules."""

import hashlib
import importlib.util
import json

import pandas as pd
import pytest

from app.data import fixes
from app.data.change_log import ChangeLog
from app.data.fixes import FixAction
from app.data.loader import load_file, with_source_rows
from app.data.rules import default_rules, expand_rules, quality_score, validate
from app.paths import messy_sample_dir, resource_path

SMALL = {"customers": 200, "products": 40, "sales_rows": 500, "returns": 60, "large_rows": 0}
RULES = default_rules()


def _generator():
    spec = importlib.util.spec_from_file_location("make_sample_data", resource_path("scripts", "make_sample_data.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _generator()


@pytest.fixture(scope="module")
def generated():
    return gen.generate(**SMALL)


def _digests(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


# ---------------------------------------------------------------------- generator
def test_same_arguments_give_byte_identical_csv_and_manifest(tmp_path):
    args = ["--customers", "200", "--products", "40", "--sales-rows", "500", "--returns", "60",
            "--large-rows", "800", "--format", "csv", "--prefix", "t"]
    assert gen.main([str(tmp_path / "a"), *args]) == 0
    assert gen.main([str(tmp_path / "b"), *args]) == 0
    first, second = _digests(tmp_path / "a"), _digests(tmp_path / "b")
    assert first == second
    assert set(first) == {"t_1_stores.csv", "t_2_customers.csv", "t_3_products.csv", "t_4_sales.csv",
                          "t_5_returns.csv", "t_6_sales.large.csv", "manifest.json"}
    manifest = json.loads((tmp_path / "a" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["sales_large"]["rows"] == 808  # 800 + 1% duplicates
    assert any("BEFORE duplicate rows are appended" in rule for rule in manifest["counting_rules"])


def test_large_file_can_be_skipped_and_returns_are_capped(tmp_path, caplog):
    tables, info = gen.generate(customers=10, products=5, sales_rows=20, returns=500, large_rows=0)
    assert "sales_large" not in tables
    # Asking for more returns than there are returnable past orders used to crash; it is capped now.
    assert info["returns"]["rows_before_duplicates"] <= 20
    assert "can be returned" in caplog.text
    with pytest.raises(ValueError):
        gen.generate(customers=0)


def test_keys_and_planted_problems_match_the_manifest(generated):
    tables, info = generated
    # Duplicates are exact copies of earlier rows (every original row has a unique id), so dropping
    # exact duplicates gives back the rows the manifest counted.
    sales = tables["sales"].drop_duplicates()
    returns = tables["returns"].drop_duplicates()
    assert len(sales) == info["sales"]["rows_before_duplicates"]
    assert len(returns) == info["returns"]["rows_before_duplicates"]
    planted = info["sales"]["problems"]
    products = set(tables["products"]["product_id"])
    customers = set(tables["customers"]["customer_id"])
    stores = set(tables["stores"]["store_id"])

    product = sales["product_id"]
    assert ((product != product.str.upper()) & product.str.upper().isin(products)).sum() == \
        planted["product_id lower case (join breaks)"]
    assert (~product.str.strip().str.upper().isin(products)).sum() == planted["orphan product_id"]
    customer = sales["customer_id"]
    assert ((customer != customer.str.strip()) & customer.str.strip().isin(customers)).sum() == \
        planted["customer_id leading space (join breaks)"]
    assert (~customer.str.strip().isin(customers)).sum() == planted["orphan customer_id"]
    assert (~sales["store_id"].isin(stores)).sum() == planted["orphan store_id"]
    assert (~returns["order_id"].isin(set(sales["order_id"]))).sum() == info["returns"]["problems"]["orphan order_id"]
    # Every key outside the planted problems resolves.
    clean = sales[product.isin(products) & customer.isin(customers) & sales["store_id"].isin(stores)]
    assert len(clean) == len(sales) - sum(planted[k] for k in (
        "product_id lower case (join breaks)", "orphan product_id", "customer_id leading space (join breaks)",
        "orphan customer_id", "orphan store_id")) + _overlaps(sales, products, customers, stores)

    quantity = pd.to_numeric(sales["quantity"])
    assert (quantity == -1).sum() == planted["negative quantity"]
    assert (quantity == 0).sum() == planted["zero quantity"]
    assert quantity.isna().sum() == planted["missing quantity"]
    discount = pd.to_numeric(sales["discount_pct"])
    assert (~discount.between(0, 100) & discount.notna()).sum() == planted["discount outside 0-100"]
    assert (sales["order_date"] == gen.FUTURE_DATE).sum() == planted["future date"]
    assert (~sales["payment_method"].isin(gen.PAYMENTS)).sum() == planted["payment_method variants"]


def _overlaps(sales, products, customers, stores) -> int:
    """Rows with key problems on two or more columns (different columns may share a row)."""
    bad = pd.concat([~sales["product_id"].isin(products), ~sales["customer_id"].isin(customers),
                     ~sales["store_id"].isin(stores)], axis=1).sum(axis=1)
    return int((bad - 1).clip(lower=0).sum())


# ---------------------------------------------------------------------- the bundled files
MANIFEST = json.loads((messy_sample_dir() / "manifest.json").read_text(encoding="utf-8"))
BUNDLED = [(name, file) for name, table in MANIFEST["tables"].items() for file in table["files"]]


def test_bundled_set_is_small_and_complete():
    names = {p.name for p in messy_sample_dir().iterdir()}
    assert names == {"retail_messy_1_stores.xlsx", "retail_messy_2_customers.xlsx", "retail_messy_3_products.xlsx",
                     "retail_messy_4_sales.xlsx", "retail_messy_4_sales.csv", "retail_messy_5_returns.xlsx",
                     "manifest.json", "README.md"}
    assert sum(p.stat().st_size for p in messy_sample_dir().iterdir()) < 2 * 1024 * 1024
    assert MANIFEST["arguments"] == {"customers": 1000, "products": 120, "sales_rows": 3000, "returns": 300,
                                     "large_rows": 0, "prefix": "retail_messy", "format": "xlsx", "sales_csv": True}


@pytest.mark.parametrize("table, file", BUNDLED, ids=[f for _t, f in BUNDLED])
def test_every_bundled_file_loads_with_the_expected_rows(table, file):
    result = load_file(messy_sample_dir() / file)
    assert len(result.df) == MANIFEST["tables"][table]["rows"]
    assert result.warnings == []


@pytest.mark.parametrize("file", ["retail_messy_4_sales.xlsx", "retail_messy_4_sales.csv"])
def test_default_rules_find_the_planted_sales_problems(file):
    df = load_file(messy_sample_dir() / file).df
    summary, _flags = validate(df, RULES)
    count = summary.set_index("check")["count"]
    table = MANIFEST["tables"]["sales"]
    planted, duplicates = table["problems"], table["duplicate_rows"]

    def between(check, expected):
        # A duplicated row copies its problems, so a check may find up to `duplicates` more.
        assert expected <= count[check] <= expected + duplicates, (check, count[check], expected)

    between("Negative values: quantity", planted["negative quantity"])  # quantity -1
    between("Missing values: quantity", planted["missing quantity"])
    between("Missing values: unit_price", planted["missing unit_price"])  # missing price is flagged
    between("discount_pct outside 0-100", planted["discount outside 0-100"])  # 110, 150 and -10
    between("Missing values: discount_pct", planted["missing discount"])
    between("Missing values: order_date", planted["missing date"])
    between("Invalid dates: order_date", planted["date as DD/MM/YYYY"])  # DD/MM/YYYY text is not a valid ISO date
    # Every appended duplicate is an exact copy of an earlier row with the same order_id.
    assert count["Exact duplicate rows"] == duplicates == count["Duplicate order_id"]
    # payment_method variants differ only in case or spacing. The CSV reader drops leading spaces
    # (" cash" becomes "cash"), so the CSV can show a few fewer than were planted.
    variants = planted["payment_method variants"]
    assert variants - 10 <= count["Text variants: payment_method"] <= variants + duplicates
    # Lower-case product ids (join breakers) are text variants of the real ids.
    between("Text variants: product_id", planted["product_id lower case (join breaks)"])
    # Known gaps of the default rule set, documented in sample_data/messy/README.md: zero quantity and
    # zero price pass the ">= 0" range rules, and no default rule checks for future dates.
    assert count["Negative values: unit_price"] == 0 and planted["zero unit_price"] > 0
    assert planted["future date"] > 0 and "future" not in " ".join(summary["check"]).lower()


def test_fixes_on_the_bundled_sales_table():
    loaded = load_file(messy_sample_dir() / "retail_messy_4_sales.xlsx")
    start = with_source_rows(loaded.df, loaded.source_rows)
    rules = {r.name: r for r in expand_rules(RULES, start)}
    log = ChangeLog()

    def score(df):
        summary, flags = validate(df, RULES)
        return quality_score(RULES, summary, flags).overall

    initial = score(start)
    working = start
    payment = rules["Text variants: payment_method"]
    mapping = {value: suggestion for value, _count, suggestion in fixes.mapping_candidates(working, payment)}
    assert mapping and all(target in gen.PAYMENTS for target in mapping.values())
    steps = [(payment, FixAction.MAP_VALUES, {"mapping": mapping}),
             (rules["Text variants: product_id"], FixAction.TRIM_AND_CASE, {"case": "upper"}),
             (rules["Exact duplicate rows"], FixAction.FIX_DUPLICATES, None)]
    states = [working]
    for rule, action, params in steps:
        new, entry = fixes.run_fix(working, rule, action, params, RULES)
        assert entry.rows_affected > 0
        assert entry.quality_score_after >= entry.quality_score_before
        log.add(entry, working)
        working = new
        states.append(working)
    assert set(working["payment_method"].dropna()) <= set(gen.PAYMENTS)
    assert working["product_id"].str.fullmatch(r"P\d{4}").all()  # join breakers repaired
    assert not working.duplicated().any()
    assert score(working) > initial  # removing the duplicates raises the uniqueness score
    assert [e.action for e in log.entries] == ["MAP_VALUES", "TRIM_AND_CASE", "FIX_DUPLICATES"]
    for expected in reversed(states[:-1]):
        _entry, restored = log.undo_last()
        pd.testing.assert_frame_equal(restored, expected)
    pd.testing.assert_frame_equal(states[0], start)
