"""Guided fixes for failed quality rules. No Qt here.

Every action is a pure function: it takes the working DataFrame (indexed by
source_row) and returns (new DataFrame, ChangeEntry). The input is never
modified, so a preview is simply a call whose result is not kept. Actions are
deterministic and idempotent: applying the same fix again changes nothing and
reports 0 rows.

Actions
    EXCLUDE_ROWS     remove the rows failing the rule from the working copy
    SET_MISSING      empty the offending cells of the rule's column (rows stay)
    MAP_VALUES       replace values using a mapping table (current -> replacement)
    FILL_CONSTANT    fill the missing / offending cells with one checked value
    TRIM_AND_CASE    trim spaces and optionally standardize letter case (text)
    FIX_DUPLICATES   remove duplicate rows, keeping the first occurrence

Nothing here runs by itself: the app shows a preview and applies a fix only
after the user confirms. Outlier rules get no actions (they are warnings only).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from enum import Enum

import numpy as np
import pandas as pd

from app.analytics.kpis import KpiInputError, prepare_sales_frame, validity_summary
from app.data.change_log import SAMPLE_LIMIT, ChangeEntry, capped_rows
from app.data.cleaner import parse_dates, parse_numeric
from app.data.normalize import clean_spacing, variant_key, variant_mapping
from app.data.rules import ALL_COLUMNS, Rule, quality_score, rule_failures, validate

MISSING_TEXT = "(missing)"
CASE_OPTIONS = {"keep": "Keep case", "upper": "UPPER CASE", "lower": "lower case", "title": "Title Case"}


class FixError(Exception):
    """Raised with a plain-language message when a fix cannot be applied as asked."""


class FixAction(str, Enum):
    EXCLUDE_ROWS = "EXCLUDE_ROWS"
    SET_MISSING = "SET_MISSING"
    MAP_VALUES = "MAP_VALUES"
    FILL_CONSTANT = "FILL_CONSTANT"
    TRIM_AND_CASE = "TRIM_AND_CASE"
    FIX_DUPLICATES = "FIX_DUPLICATES"


DESCRIPTIONS = {
    FixAction.EXCLUDE_ROWS: "Exclude the failing rows from the working copy",
    FixAction.SET_MISSING: "Set the offending values to missing (rows stay)",
    FixAction.MAP_VALUES: "Replace values using a mapping table",
    FixAction.FILL_CONSTANT: "Fill the cells with one value you enter",
    FixAction.TRIM_AND_CASE: "Trim spaces and standardize letter case",
    FixAction.FIX_DUPLICATES: "Remove duplicates, keeping the first occurrence",
}

# Actions offered per rule type, most fitting first. Every rule type in the rule file
# format has an entry (tests check the default rule set against it). Outliers are
# warnings only: no action. A rule on column "*" only gets row-level actions.
ACTIONS_BY_TYPE: dict[str, list[FixAction]] = {
    "not_null": [FixAction.FILL_CONSTANT, FixAction.EXCLUDE_ROWS],
    "unique": [FixAction.FIX_DUPLICATES],
    "dtype": [FixAction.SET_MISSING, FixAction.MAP_VALUES, FixAction.FILL_CONSTANT, FixAction.EXCLUDE_ROWS],
    "range": [FixAction.SET_MISSING, FixAction.FILL_CONSTANT, FixAction.EXCLUDE_ROWS],
    "allowed_values": [FixAction.MAP_VALUES, FixAction.SET_MISSING, FixAction.FILL_CONSTANT, FixAction.EXCLUDE_ROWS],
    "regex": [FixAction.MAP_VALUES, FixAction.SET_MISSING, FixAction.FILL_CONSTANT, FixAction.EXCLUDE_ROWS],
    "cross_column": [FixAction.SET_MISSING, FixAction.EXCLUDE_ROWS],
    "outlier": [],
    "text_variants": [FixAction.MAP_VALUES, FixAction.TRIM_AND_CASE],
}
FALLBACK_ACTIONS = [FixAction.EXCLUDE_ROWS, FixAction.SET_MISSING]
ROW_ACTIONS = {FixAction.EXCLUDE_ROWS, FixAction.FIX_DUPLICATES}
TEXT_ONLY_ACTIONS = {FixAction.MAP_VALUES, FixAction.TRIM_AND_CASE}


def suggested_actions(rule: Rule, column_dtype=None) -> list[FixAction]:
    """Actions that make sense for this rule; column_dtype (the rule column's dtype) drops
    text-only actions for real number or date columns."""
    actions = list(ACTIONS_BY_TYPE.get(rule.type, FALLBACK_ACTIONS))
    if rule.column == ALL_COLUMNS:
        actions = [a for a in actions if a in ROW_ACTIONS]
    if column_dtype is not None and (pd.api.types.is_numeric_dtype(column_dtype)
                                     or pd.api.types.is_datetime64_any_dtype(column_dtype)):
        actions = [a for a in actions if a not in TEXT_ONLY_ACTIONS]
    return actions


def rule_columns(rule: Rule) -> list[str]:
    if rule.type == "unique":
        return list(rule.params.get("columns", []))
    if rule.type == "cross_column":
        return [rule.column, rule.params["other"]]
    return [] if rule.column == ALL_COLUMNS else [rule.column]


# ---------------------------------------------------------------------- helpers
def display(value) -> str:
    """A cell value as shown in previews and the log."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return MISSING_TEXT
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _same(a, b) -> bool:
    a_missing = a is None or (not isinstance(a, str) and pd.isna(a))
    b_missing = b is None or (not isinstance(b, str) and pd.isna(b))
    if a_missing or b_missing:
        return a_missing and b_missing
    return type(a) is type(b) and a == b if isinstance(a, str) or isinstance(b, str) else a == b


def _missing_for(series: pd.Series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.NaT
    if pd.api.types.is_float_dtype(series):
        return np.nan
    if isinstance(series.dtype, pd.StringDtype):
        return pd.NA
    return None


def column_kind(series: pd.Series, rule: Rule | None = None) -> str:
    """numeric, date or text: what a value entered for this column must be."""
    if rule is not None and rule.type == "dtype":
        return "date" if rule.params.get("kind") == "date" else "numeric"
    if rule is not None and rule.type in ("range", "outlier"):
        return "numeric"
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    present = series.dropna()
    present = present[present.astype(str).str.strip() != ""]
    if len(present) == 0:
        return "text"
    if parse_numeric(present).notna().mean() >= 0.8:
        return "numeric"
    if parse_dates(present).notna().mean() >= 0.8:
        return "date"
    return "text"


def parse_constant(text: str, kind: str, column: str):
    """The value to fill for kind (numeric, date, text), or FixError with a friendly message."""
    text = (text or "").strip()
    if not text:
        raise FixError("Enter a value to fill in.")
    if kind == "numeric":
        value = parse_numeric(pd.Series([text])).iloc[0]
        if pd.isna(value):
            raise FixError(f"{column} holds numbers: \"{text}\" is not a number. Enter a value such as 0 or 12.5.")
        return float(value)
    if kind == "date":
        value = parse_dates(pd.Series([text])).iloc[0]
        if pd.isna(value):
            raise FixError(f"{column} holds dates: \"{text}\" is not a valid date. Enter a date as YYYY-MM-DD.")
        return value.strftime("%Y-%m-%d")
    return text


def _set_cells(df: pd.DataFrame, index, column: str, value) -> pd.DataFrame:
    """df with df.loc[index, column] = value, widening the column type when it cannot hold value."""
    series = df[column]
    is_text_column = pd.api.types.is_object_dtype(series) or isinstance(series.dtype, pd.StringDtype)
    if isinstance(value, pd.Series):
        if not is_text_column:
            df[column] = series.astype(object)
    elif value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        if pd.api.types.is_integer_dtype(series) or pd.api.types.is_bool_dtype(series):
            df[column] = series.astype("float64")
    elif isinstance(value, str) and not is_text_column:
        df[column] = series.astype(object)
    elif isinstance(value, float) and pd.api.types.is_integer_dtype(series):
        df[column] = series.astype("float64")
    df.loc[index, column] = value
    return df


def _cell_samples(before: pd.DataFrame, after: pd.DataFrame, cells: list[tuple]) -> list[dict]:
    return [{"source_row": _label(row), "column": column, "before": display(before.at[row, column]),
             "after": display(after.at[row, column])} for row, column in cells[:SAMPLE_LIMIT]]


def _label(row):
    return int(row) if isinstance(row, (int, np.integer)) else row


def _row_summary(df: pd.DataFrame, row, columns: list[str]) -> str:
    shown = columns or list(df.columns)[:4]
    return ", ".join(f"{c}={display(df.at[row, c])}" for c in shown if c in df.columns)


def _entry(rule: Rule, action: FixAction, columns: list[str], parameters: dict, rows: list, cells: int,
           samples: list[dict]) -> ChangeEntry:
    stored, total, truncated = capped_rows(_label(r) for r in rows)
    return ChangeEntry(rule=rule.name, action=action.value, columns=columns, parameters=parameters,
                       rows_affected=total, cells_changed=cells, source_rows=stored, source_rows_total=total,
                       truncated=truncated, samples=samples)


def _require_column(rule: Rule, df: pd.DataFrame, action: FixAction) -> str:
    if rule.column == ALL_COLUMNS:
        raise FixError(f"{DESCRIPTIONS[action]} needs a rule on one column; \"{rule.name}\" covers all columns.")
    if rule.column not in df.columns:
        raise FixError(f"Column {rule.column} is not in the working data.")
    return rule.column


def _changed_cells(before: pd.DataFrame, after: pd.DataFrame, column: str, rows) -> list:
    return [row for row in rows if not _same(before.at[row, column], after.at[row, column])]


# ---------------------------------------------------------------------- actions
def exclude_rows(df: pd.DataFrame, rule: Rule) -> tuple[pd.DataFrame, ChangeEntry]:
    """Remove the rows failing rule from the working copy (their source rows are logged)."""
    failing = rule_failures(df, rule)
    rows = list(df.index[failing.to_numpy(dtype=bool)])
    out = df.drop(index=rows)
    columns = rule_columns(rule)
    samples = [{"source_row": _label(r), "column": ", ".join(columns) or "(row)",
                "before": _row_summary(df, r, columns), "after": "(row excluded)"} for r in rows[:SAMPLE_LIMIT]]
    return out, _entry(rule, FixAction.EXCLUDE_ROWS, columns, {}, rows, 0, samples)


def set_missing(df: pd.DataFrame, rule: Rule) -> tuple[pd.DataFrame, ChangeEntry]:
    """Empty the rule column's cells in the failing rows; the rows stay."""
    column = _require_column(rule, df, FixAction.SET_MISSING)
    failing = rule_failures(df, rule)
    rows = [r for r in df.index[failing.to_numpy(dtype=bool)]
            if not _same(df.at[r, column], _missing_for(df[column]))]
    out = df.copy()
    if rows:
        out = _set_cells(out, rows, column, _missing_for(out[column]))
    changed = _changed_cells(df, out, column, rows)
    return out, _entry(rule, FixAction.SET_MISSING, [column], {}, changed, len(changed),
                       _cell_samples(df, out, [(r, column) for r in changed]))


def map_values(df: pd.DataFrame, rule: Rule, mapping: dict[str, str]) -> tuple[pd.DataFrame, ChangeEntry]:
    """Replace values in the rule's column using mapping {current value: replacement}.

    Keys match the cell text ignoring case and extra spaces ("new york " matches "New York");
    rows with an empty replacement are ignored and values not in the table stay as they are.
    """
    column = _require_column(rule, df, FixAction.MAP_VALUES)
    targets = {variant_key(str(k)): str(v).strip() for k, v in mapping.items()
               if v is not None and str(v).strip() != ""}
    used = {str(k): str(v).strip() for k, v in mapping.items() if v is not None and str(v).strip() != ""}
    out = df.copy()
    rows = []
    if targets:
        series = df[column]
        is_text = series.map(lambda v: isinstance(v, str))
        keys = series.where(is_text).map(lambda v: variant_key(v) if isinstance(v, str) else None)
        new = keys.map(targets)
        hit = new.notna() & (new != series)
        rows = list(df.index[hit.to_numpy(dtype=bool)])
        if rows:
            out = _set_cells(out, rows, column, new[hit])
    changed = _changed_cells(df, out, column, rows)
    return out, _entry(rule, FixAction.MAP_VALUES, [column], {"mapping": used}, changed, len(changed),
                       _cell_samples(df, out, [(r, column) for r in changed]))


def fill_constant(df: pd.DataFrame, rule: Rule, value: str) -> tuple[pd.DataFrame, ChangeEntry]:
    """Fill the rule column's cells in the failing rows with value (checked against the column type)."""
    column = _require_column(rule, df, FixAction.FILL_CONSTANT)
    kind = column_kind(df[column], rule)
    constant = parse_constant(value, kind, column)
    if kind == "numeric" and not pd.api.types.is_numeric_dtype(df[column]):
        stored = display(constant)  # a text column of numbers keeps text: "5", not 5.0
    elif kind == "date" and pd.api.types.is_datetime64_any_dtype(df[column]):
        stored = pd.Timestamp(constant)
    else:
        stored = constant
    failing = rule_failures(df, rule)
    rows = list(df.index[failing.to_numpy(dtype=bool)])
    out = df.copy()
    if rows:
        out = _set_cells(out, rows, column, stored)
        still = rule_failures(out, rule).loc[rows]
        if still.any():
            raise FixError(f"\"{value.strip()}\" would still fail the rule \"{rule.name}\". "
                           "Enter a value the rule accepts.")
    changed = _changed_cells(df, out, column, rows)
    return out, _entry(rule, FixAction.FILL_CONSTANT, [column], {"value": stored, "kind": kind}, changed,
                       len(changed), _cell_samples(df, out, [(r, column) for r in changed]))


def trim_and_case(df: pd.DataFrame, rule: Rule, case: str = "keep") -> tuple[pd.DataFrame, ChangeEntry]:
    """Trim and collapse spaces in the rule column's text values; optionally upper/lower/title case."""
    column = _require_column(rule, df, FixAction.TRIM_AND_CASE)
    if case not in CASE_OPTIONS:
        raise FixError(f"Unknown case option: {case}.")

    def convert(value):
        if not isinstance(value, str):
            return value
        text = clean_spacing(value)
        return {"upper": text.upper(), "lower": text.lower(), "title": text.title()}.get(case, text)

    series = df[column]
    new = series.map(convert)
    hit = series.map(lambda v: isinstance(v, str)) & (new != series)
    rows = list(df.index[hit.to_numpy(dtype=bool)])
    out = df.copy()
    if rows:
        out = _set_cells(out, rows, column, new[hit])
    return out, _entry(rule, FixAction.TRIM_AND_CASE, [column], {"case": case}, rows, len(rows),
                       _cell_samples(df, out, [(r, column) for r in rows]))


def fix_duplicates(df: pd.DataFrame, rule: Rule) -> tuple[pd.DataFrame, ChangeEntry]:
    """Remove duplicate rows (on the rule's key columns, or whole rows), keeping the first occurrence."""
    keys = [c for c in rule.params.get("columns", []) if c in df.columns] if rule.type == "unique" else []
    subset = keys or list(df.columns)
    duplicated = df.duplicated(subset=subset, keep="first")
    rows = list(df.index[duplicated.to_numpy(dtype=bool)])
    first = {}
    for label, key in zip(df.index, map(tuple, df[subset].astype(object).where(df[subset].notna(), None)
                                         .itertuples(index=False, name=None))):
        first.setdefault(key, label)
    kept = {}
    if rows:
        frame = df.loc[rows, subset].astype(object).where(df.loc[rows, subset].notna(), None)
        kept = {_label(r): _label(first[k]) for r, k in zip(rows, frame.itertuples(index=False, name=None))}
    out = df.drop(index=rows)
    samples = [{"source_row": _label(r), "column": ", ".join(keys) or "(whole row)",
                "before": _row_summary(df, r, keys), "after": f"(removed; row {kept[_label(r)]} kept)"}
               for r in rows[:SAMPLE_LIMIT]]
    kept_sample = dict(list(kept.items())[:SAMPLE_LIMIT * 25])
    return out, _entry(rule, FixAction.FIX_DUPLICATES, keys, {"key_columns": keys or "whole row",
                                                             "kept_row_for_removed": kept_sample},
                       rows, 0, samples)


# ---------------------------------------------------------------------- running a fix
@dataclass
class Measure:
    quality_score: float | None
    valid_rows: int | None


def measure(df: pd.DataFrame, rules: list[Rule], unavailable: Collection[str] = ()) -> Measure:
    """Overall quality score (as on the Data Quality tab) and KPI-valid rows of df."""
    summary, flags = validate(df, rules)
    score = quality_score(rules, summary, flags).overall
    try:
        valid = validity_summary(prepare_sales_frame(df, unavailable)).kpi_rows
    except KpiInputError:
        valid = None
    return Measure(score, valid)


def apply_action(df: pd.DataFrame, rule: Rule, action: FixAction, params: dict | None = None
                 ) -> tuple[pd.DataFrame, ChangeEntry]:
    params = params or {}
    action = FixAction(action)
    if action is FixAction.EXCLUDE_ROWS:
        return exclude_rows(df, rule)
    if action is FixAction.SET_MISSING:
        return set_missing(df, rule)
    if action is FixAction.MAP_VALUES:
        return map_values(df, rule, params.get("mapping", {}))
    if action is FixAction.FILL_CONSTANT:
        return fill_constant(df, rule, params.get("value", ""))
    if action is FixAction.TRIM_AND_CASE:
        return trim_and_case(df, rule, params.get("case", "keep"))
    return fix_duplicates(df, rule)


def run_fix(df: pd.DataFrame, rule: Rule, action: FixAction, params: dict | None, rules: list[Rule],
            unavailable: Collection[str] = (), before: Measure | None = None) -> tuple[pd.DataFrame, ChangeEntry]:
    """apply_action plus the quality score and KPI-valid rows before and after. df is not modified:
    a preview is a call whose result is not kept."""
    if FixAction(action) not in suggested_actions(rule) and not (
            rule.type not in ACTIONS_BY_TYPE and FixAction(action) in FALLBACK_ACTIONS):
        raise FixError(f"{DESCRIPTIONS[FixAction(action)]} is not offered for \"{rule.name}\".")
    before = before or measure(df, rules, unavailable)
    out, entry = apply_action(df, rule, action, params)
    after = measure(out, rules, unavailable) if entry.rows_affected else before
    entry = replace(entry, quality_score_before=before.quality_score, quality_score_after=after.quality_score,
                    valid_rows_before=before.valid_rows, valid_rows_after=after.valid_rows)
    return out, entry


def what_it_will_do(entry: ChangeEntry) -> str:
    """One sentence from the real counts of a previewed fix."""
    if entry.rows_affected == 0:
        return "Nothing would change: no row needs this fix."
    rows = f"{entry.rows_affected:,} row{'s' if entry.rows_affected != 1 else ''}"
    column = ", ".join(entry.columns)
    if entry.action == FixAction.EXCLUDE_ROWS.value:
        return f"{rows} will be excluded from the working copy (Undo or Reset brings them back)."
    if entry.action == FixAction.FIX_DUPLICATES.value:
        return f"{rows} will be removed as duplicates; the first occurrence of each is kept."
    if entry.action == FixAction.SET_MISSING.value:
        return f"{entry.cells_changed:,} value(s) in {column} will be set to missing; the rows stay."
    if entry.action == FixAction.FILL_CONSTANT.value:
        return f"{entry.cells_changed:,} cell(s) in {column} will be set to {entry.parameters.get('value')}."
    if entry.action == FixAction.MAP_VALUES.value:
        return f"{entry.cells_changed:,} value(s) in {column} will be replaced using the mapping table."
    return f"{entry.cells_changed:,} value(s) in {column} will be trimmed or re-cased."


def mapping_candidates(df: pd.DataFrame, rule: Rule) -> list[tuple[str, int, str]]:
    """(current value, count, suggested replacement) for the mapping table: the distinct values of
    the failing cells, most frequent first. Text variants get their normalized form as suggestion."""
    if rule.column == ALL_COLUMNS or rule.column not in df.columns:
        return []
    series = df[rule.column]
    failing = series[rule_failures(df, rule).to_numpy(dtype=bool)].dropna()
    counts = failing.astype(str).value_counts(sort=False)
    suggestions = variant_mapping(series) if rule.type == "text_variants" else {}
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(value, int(count), suggestions.get(value, "")) for value, count in ordered]


def explain(rule: Rule) -> str:
    """The problem a rule finds, in one plain sentence."""
    column, p = rule.column, rule.params
    if rule.type == "not_null":
        return "Some rows have no value at all." if column == ALL_COLUMNS else f"Some rows have no value in {column}."
    if rule.type == "unique":
        keys = p.get("columns") or []
        return (f"Some rows repeat the {', '.join(keys)} of an earlier row." if keys
                else "Some rows are exact copies of an earlier row.")
    if rule.type == "dtype":
        what = "dates (YYYY-MM-DD)" if p.get("kind") == "date" else "numbers"
        return f"Some values in {column} cannot be read as {what}."
    if rule.type == "range":
        bounds = [f"at least {p['min']}" if p.get("min") is not None else "",
                  f"at most {p['max']}" if p.get("max") is not None else ""]
        return f"Some values in {column} are outside the allowed range ({' and '.join(b for b in bounds if b)})."
    if rule.type == "allowed_values":
        return f"Some values in {column} are not in the list of allowed values."
    if rule.type == "regex":
        return f"Some values in {column} do not match the expected pattern."
    if rule.type == "cross_column":
        return f"In some rows, \"{column} {p.get('operator')} {p.get('other')}\" is not true."
    if rule.type == "outlier":
        return f"Some values in {column} are far from the rest. This is a warning only: check them by hand."
    if rule.type == "text_variants":
        column = "a text column" if column == ALL_COLUMNS else column
        return f"Some values in {column} differ from another value only in case or spacing."
    return rule.description or "Some rows fail this check."


def entry_from_frames(before: pd.DataFrame, after: pd.DataFrame, rule_name: str, action: str,
                      columns: list[str], parameters: dict) -> ChangeEntry:
    """A ChangeEntry describing the cell changes between two versions of the working copy
    (same rows), e.g. for the existing Normalize text step."""
    cells: list[tuple] = []
    for column in columns:
        cells += [(row, column) for row in before.index
                  if not _same(before.at[row, column], after.at[row, column])]
    changed = {row for row, _column in cells}
    rows = [row for row in before.index if row in changed]
    stored, total, truncated = capped_rows(_label(r) for r in rows)
    return ChangeEntry(rule=rule_name, action=action, columns=list(columns), parameters=parameters,
                       rows_affected=total, cells_changed=len(cells), source_rows=stored,
                       source_rows_total=total, truncated=truncated, samples=_cell_samples(before, after, cells))
