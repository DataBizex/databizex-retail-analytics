import json

import pandas as pd
import pytest

from app.data.cleaner import STATUS_ISSUE, STATUS_OK, STATUS_UNAVAILABLE, remove_exact_duplicates
from app.data.loader import load_file
from app.data.rules import (
    Rule,
    RulesError,
    default_rules,
    detect_outliers,
    load_rules,
    outlier_mask,
    quality_score,
    validate,
)
from app.database.duckdb_manager import DuckDBManager
from app.paths import sample_data_path

# Hand-checked HR fixture (the engine is not tied to retail columns):
#   row 1: salary "abc" (not numeric), hired 2021-13-01 (invalid date)
#   row 2: salary -10, dept "it" (wrong case), left before hired
#   row 3: duplicate id E3
#   row 4: dept missing, email missing
HR = pd.DataFrame({
    "id": ["E1", "E2", "E3", "E3", "E5"],
    "dept": ["HR", "IT", "it", "Sales", None],
    "salary": ["50000", "abc", "-10", "70000", "60000"],
    "email": ["a@x.com", "bad", "c@x.com", "d@x.com", None],
    "hired": ["2020-01-01", "2021-13-01", "2019-05-05", "2022-02-02", "2023-03-03"],
    "left": ["2021-01-01", "2022-01-01", "2018-01-01", None, "2024-01-01"],
})


def _one(rule: Rule, df: pd.DataFrame = HR) -> tuple[pd.Series, pd.Series]:
    summary, flags = validate(df, [rule])
    return summary.iloc[0], flags[rule.name]


def test_not_null():
    row, flags = _one(Rule("dept missing", "dept", "not_null"))
    assert row["count"] == 1 and row["status"] == STATUS_ISSUE
    assert list(flags) == [False, False, False, False, True]


def test_dtype_numeric_and_date():
    row, flags = _one(Rule("salary numeric", "salary", "dtype", {"kind": "numeric"}))
    assert row["count"] == 1 and flags[1]
    row, flags = _one(Rule("hired date", "hired", "dtype", {"kind": "date"}))
    assert row["count"] == 1 and flags[1]


def test_range():
    row, flags = _one(Rule("salary >= 0", "salary", "range", {"min": 0}))
    assert row["count"] == 1 and flags[2]  # -10; "abc" is a dtype problem, not a range one
    row, _ = _one(Rule("salary <= 65000", "salary", "range", {"min": 0, "max": 65000}))
    assert row["count"] == 2  # -10 and 70000


def test_allowed_values():
    rule = Rule("dept list", "dept", "allowed_values", {"values": ["HR", "IT", "Sales"]})
    row, flags = _one(rule)
    assert row["count"] == 1 and flags[2]  # "it"; the missing dept is not counted
    rule.params["case_sensitive"] = False
    assert _one(rule)[0]["count"] == 0


def test_regex():
    row, flags = _one(Rule("email format", "email", "regex", {"pattern": r"[^@\s]+@[^@\s]+\.\w+"}))
    assert row["count"] == 1 and flags[1]


def test_unique_on_key_columns_and_whole_row():
    row, flags = _one(Rule("id unique", "*", "unique", {"columns": ["id"]}))
    assert row["count"] == 1 and list(flags) == [False, False, False, True, False]
    assert _one(Rule("exact duplicates", "*", "unique", {"columns": []}))[0]["count"] == 0


def test_cross_column_dates():
    rule = Rule("left after hired", "left", "cross_column",
                {"other": "hired", "operator": ">=", "compare_as": "date"})
    row, flags = _one(rule)
    # row 2 fails; row 1 (invalid hired) and row 3 (no left date) are not evaluated
    assert row["count"] == 1 and flags[2]


def test_cross_column_numeric():
    df = pd.DataFrame({"unit_price": [10, 5, 8], "unit_cost": [4, 6, 8]})
    rule = Rule("price below cost", "unit_price", "cross_column", {"other": "unit_cost", "operator": ">="})
    row, flags = _one(rule, df)
    assert row["count"] == 1 and list(flags) == [False, True, False]


def test_outlier_iqr_and_zscore():
    values = pd.Series([10, 11, 12, 10, 11, 100])
    # IQR: Q1 10.25, Q3 11.75 -> fences 8 .. 14
    assert list(outlier_mask(values, "iqr")) == [False] * 5 + [True]
    # z-score of 100 is 2.04 (mean 25.67, sample std 36.42)
    assert outlier_mask(values, "zscore", threshold=2).sum() == 1
    assert outlier_mask(values, "zscore", threshold=3).sum() == 0
    counts, flags = detect_outliers(pd.DataFrame({"v": values, "w": [1] * 6}))
    assert counts.to_dict() == {"v": 1, "w": 0}
    assert flags["v"].iloc[-1]


def test_outlier_rule_is_a_warning():
    row, _ = _one(Rule("v outliers", "v", "outlier", {"method": "iqr"}, severity="warning"),
                  pd.DataFrame({"v": [10, 11, 12, 10, 11, 100]}))
    assert row["count"] == 1 and row["severity"] == "warning"


def test_missing_column_is_unavailable_for_every_type():
    rules = [
        Rule("a", "nope", "not_null"),
        Rule("b", "nope", "dtype", {"kind": "numeric"}),
        Rule("c", "nope", "range", {"min": 0}),
        Rule("d", "nope", "allowed_values", {"values": ["x"]}),
        Rule("e", "nope", "regex", {"pattern": "x"}),
        Rule("f", "*", "unique", {"columns": ["id", "nope"]}),
        Rule("g", "left", "cross_column", {"other": "nope", "operator": ">"}),
        Rule("h", "nope", "outlier", {}, severity="warning"),
    ]
    summary, flags = validate(HR, rules)
    assert (summary["status"] == STATUS_UNAVAILABLE).all()
    assert summary["count"].isna().all()
    assert (summary["details"] == "Required column not found").all()
    assert not flags.to_numpy().any()


def test_summary_shape_and_row_flags():
    summary, flags = validate(HR, [Rule("dept missing", "dept", "not_null"),
                                   Rule("id unique", "*", "unique", {"columns": ["id"]})])
    assert list(summary.columns) == ["check", "count", "status", "severity", "details"]
    assert list(flags.columns) == list(summary["check"])
    assert flags.index.equals(HR.index)
    assert set(summary["status"]) <= {STATUS_OK, STATUS_ISSUE, STATUS_UNAVAILABLE}


def test_all_columns_not_null_expands_with_explicit_rule_taking_precedence():
    df = pd.DataFrame({"x": [1, None], "y": [None, None]})
    rules = [Rule("All missing", "*", "not_null", {"each_column": "Missing: {column}"}, severity="warning"),
             Rule("y is required", "y", "not_null", severity="error")]
    summary = validate(df, rules)[0].set_index("check")
    assert list(summary.index) == ["All missing", "Missing: x", "y is required"]
    assert summary.loc["All missing", "count"] == 3  # empty cells
    assert summary.loc["y is required", "severity"] == "error"


def test_validate_does_not_modify_input():
    before = HR.copy()
    validate(HR, default_rules())
    pd.testing.assert_frame_equal(HR, before)


def test_quality_score_formula():
    df = pd.DataFrame({"a": [1, None, 3, 4], "b": ["x", "y", "y", "z"], "c": [1, 2, "bad", -1]})
    rules = [
        Rule("a not null", "a", "not_null"),                         # row 1 fails  -> 75%
        Rule("c numeric", "c", "dtype", {"kind": "numeric"}),        # row 2 fails  \
        Rule("c >= 0", "c", "range", {"min": 0}),                    # row 3 fails  -> 50%
        Rule("b unique", "*", "unique", {"columns": ["b"]}),         # row 2 fails  -> 75%
        Rule("a outliers", "a", "outlier", {}, severity="warning"),  # not scored
        Rule("x vs y", "x", "cross_column", {"other": "y", "operator": ">"}),  # Unavailable
    ]
    summary, flags = validate(df, rules)
    score = quality_score(rules, summary, flags)
    assert score.completeness == pytest.approx(75.0)
    assert score.validity == pytest.approx(50.0)
    assert score.uniqueness == pytest.approx(75.0)
    assert score.consistency is None  # no usable cross_column rule
    assert score.overall == pytest.approx((75 + 50 + 75) / 3)


# ---------------------------------------------------------------------- rule files
def _write_rules(tmp_path, payload) -> str:
    path = tmp_path / "rules.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_load_rules_roundtrip(tmp_path):
    path = _write_rules(tmp_path, {"rules": [
        {"name": "salary >= 0", "column": "salary", "type": "range", "params": {"min": 0}},
        {"name": "salary outliers", "column": "salary", "type": "outlier"},
    ]})
    rules = load_rules(path)
    assert [r.severity for r in rules] == ["error", "warning"]
    # -10 is also an IQR outlier: Q1 37,497.5, Q3 62,500 -> lower fence -0.5
    assert validate(HR, rules)[0]["count"].tolist() == [1, 1]


@pytest.mark.parametrize("payload, message", [
    ("{not json", "not valid JSON"),
    ({"rules": []}, "non-empty"),
    ({"rules": [{"name": "x", "column": "a", "type": "magic"}]}, "type"),
    ({"rules": [{"name": "x", "column": "a", "type": "range", "params": {}}]}, "min"),
    ({"rules": [{"name": "x", "column": "a", "type": "regex", "params": {"pattern": "("}}]}, "regular expression"),
    ({"rules": [{"name": "x", "column": "a", "type": "outlier", "severity": "error"}]}, "warning"),
    ({"rules": [{"name": "x", "column": "a", "type": "not_null"}] * 2}, "unique"),
])
def test_invalid_rule_files_raise(tmp_path, payload, message):
    with pytest.raises(RulesError, match=message):
        load_rules(_write_rules(tmp_path, payload))


# ---------------------------------------------------------------------- shipped default
def test_default_rule_set_reproduces_demo_counts():
    summary, _ = validate(load_file(sample_data_path()).df, default_rules())
    count = summary.set_index("check")["count"]
    assert count["Exact duplicate rows"] == 25
    assert count["Missing values (all columns)"] == 18
    assert count["Invalid dates: order_date"] == 8
    assert sum(count[f"Invalid numeric: {c}"] for c in ["quantity", "unit_price", "discount_pct", "unit_cost"]) == 6
    assert count["discount_pct outside 0-100"] == 5
    assert sum(count[f"Negative values: {c}"] for c in ["quantity", "unit_price", "unit_cost"]) == 6


def test_default_outliers_use_iqr_3x_and_zscore_stays_available(tmp_path):
    outlier_rules = [r for r in default_rules() if r.type == "outlier"]
    assert {r.column for r in outlier_rules} == {"quantity", "unit_price"}
    assert all(r.params == {"method": "iqr", "factor": 3.0} for r in outlier_rules)

    summary, _ = validate(load_file(sample_data_path()).df, outlier_rules)
    count = summary.set_index("check")["count"]
    assert count["Outliers (IQR): quantity"] == 0      # 1 with the 1.5x fence
    assert count["Outliers (IQR): unit_price"] == 69   # 180 with the 1.5x fence

    path = _write_rules(tmp_path, {"rules": [
        {"name": "z", "column": "v", "type": "outlier", "params": {"method": "zscore", "threshold": 2},
         "comment": "z-score is still selectable in a custom rule file"},
    ]})
    rules = load_rules(path)
    assert validate(pd.DataFrame({"v": [10, 11, 12, 10, 11, 100]}), rules)[0]["count"].iloc[0] == 1


def test_outliers_are_flagged_but_kpis_unchanged():
    df = load_file(sample_data_path()).df
    before = df.copy()
    summary, flags = validate(df, default_rules())
    outliers = summary[summary["check"].str.startswith("Outliers")]
    assert (outliers["severity"] == "warning").all()
    assert outliers["count"].sum() > 0
    assert flags[outliers["check"].tolist()].to_numpy().any()
    pd.testing.assert_frame_equal(df, before)  # nothing removed or changed

    cleaned, _ = remove_exact_duplicates(df)
    manager = DuckDBManager()
    manager.load_sales(cleaned)
    net = manager.headline()["net_revenue"]
    manager.close()
    assert round(net, 2) == 405163.05
