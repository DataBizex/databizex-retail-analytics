"""Rule-based data validation: the quality checks come from a JSON rule file.

No Qt here, so every rule type can be tested on its own. Rules only count and
flag problems; they never change data or drop rows.

Rule file format (JSON, so no extra dependency is needed):

    {"rules": [
      {"name": "Negative values: quantity", "column": "quantity", "type": "range",
       "params": {"min": 0}, "severity": "error", "description": "optional details text",
       "comment": "optional note for rule authors; not shown in the app"}
    ]}

Rule types and their params:
    not_null        -                       column "*" = all cells; params.each_column
                                            (e.g. "Missing values: {column}") adds one row per column
    dtype           kind: numeric | date    non-empty values that cannot be read as that type
    range           min and/or max          numeric values outside [min, max] (bounds allowed)
    allowed_values  values: [...], case_sensitive (default true)
    regex           pattern                 non-empty text that does not fully match
    unique          columns: [...]          duplicates on these key columns; empty = whole row
    cross_column    other, operator (< <= > >= == !=), compare_as: numeric | date
                                            rows where "column operator other" is false
    outlier         method: iqr (factor, default 1.5) | zscore (threshold, default 3)
                                            always severity "warning"; outliers are never removed
"""

from __future__ import annotations

import json
import logging
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.data.cleaner import STATUS_ISSUE, STATUS_OK, STATUS_UNAVAILABLE, parse_dates, parse_numeric
from app.paths import resource_path

logger = logging.getLogger(__name__)

RULE_TYPES = ("not_null", "dtype", "range", "allowed_values", "regex", "unique", "cross_column", "outlier")
SEVERITIES = ("error", "warning")
ALL_COLUMNS = "*"
OPERATORS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
             "==": operator.eq, "!=": operator.ne}
# Quality-score dimension of each rule type; outliers are not scored.
DIMENSIONS = {
    "not_null": "completeness",
    "dtype": "validity", "range": "validity", "allowed_values": "validity", "regex": "validity",
    "unique": "uniqueness",
    "cross_column": "consistency",
}
SUMMARY_COLUMNS = ["check", "count", "status", "severity", "details"]
UNAVAILABLE_DETAILS = "Required column not found"
RULES_FILE_FILTER = "Rule set (*.json)"


class RulesError(Exception):
    """Raised with a user-readable message when a rule file cannot be used."""


@dataclass
class Rule:
    name: str
    column: str
    type: str
    params: dict = field(default_factory=dict)
    severity: str = "error"
    description: str = ""  # shown in the "details" column


@dataclass
class QualityScore:
    completeness: float | None
    validity: float | None
    uniqueness: float | None
    consistency: float | None
    overall: float | None


# ---------------------------------------------------------------------- loading
def default_rules_path() -> Path:
    return resource_path("rules", "retail_default.json")


def default_rules() -> list[Rule]:
    return load_rules(default_rules_path())


def load_rules(path: str | Path) -> list[Rule]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RulesError(f"The rule file is not valid JSON: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesError(f"Could not read the rule file: {exc}") from exc

    raw = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise RulesError('The rule file must contain a non-empty "rules" list.')
    rules = [_parse_rule(item, position) for position, item in enumerate(raw, start=1)]
    seen: set[str] = set()
    for rule in rules:
        if rule.name in seen:
            raise RulesError(f"Two rules are named '{rule.name}'. Rule names must be unique.")
        seen.add(rule.name)
    logger.info("Loaded %d rules from %s", len(rules), path)
    return rules


def _parse_rule(item: object, position: int) -> Rule:
    if not isinstance(item, dict):
        raise RulesError(f"Rule {position} must be an object with name, column and type.")
    name = item.get("name")
    label = f"Rule {position} ({name})" if name else f"Rule {position}"
    if not isinstance(name, str) or not name.strip():
        raise RulesError(f"{label}: 'name' is required.")
    rule_type = item.get("type")
    if rule_type not in RULE_TYPES:
        raise RulesError(f"{label}: 'type' must be one of {', '.join(RULE_TYPES)}.")
    column = item.get("column", ALL_COLUMNS if rule_type == "unique" else None)
    if not isinstance(column, str) or not column:
        raise RulesError(f"{label}: 'column' is required.")
    if column == ALL_COLUMNS and rule_type not in ("not_null", "unique"):
        raise RulesError(f"{label}: column '*' is only allowed for not_null and unique rules.")
    params = item.get("params", {})
    if not isinstance(params, dict):
        raise RulesError(f"{label}: 'params' must be an object.")
    severity = item.get("severity", "warning" if rule_type == "outlier" else "error")
    if severity not in SEVERITIES:
        raise RulesError(f"{label}: 'severity' must be 'error' or 'warning'.")
    if rule_type == "outlier" and severity != "warning":
        raise RulesError(f"{label}: outlier rules are always severity 'warning'.")
    description = item.get("description", "")
    if not isinstance(description, str):
        raise RulesError(f"{label}: 'description' must be text.")
    if not isinstance(item.get("comment", ""), str):
        raise RulesError(f"{label}: 'comment' must be text.")
    problem = _params_problem(rule_type, params)
    if problem:
        raise RulesError(f"{label}: {problem}")
    return Rule(name=name.strip(), column=column, type=rule_type, params=params,
                severity=severity, description=description)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _params_problem(rule_type: str, params: dict) -> str | None:
    if rule_type == "not_null":
        template = params.get("each_column")
        if template is not None and (not isinstance(template, str) or "{column}" not in template):
            return "params.each_column must be text containing {column}."
    elif rule_type == "dtype":
        if params.get("kind") not in ("numeric", "date"):
            return "params.kind must be 'numeric' or 'date'."
    elif rule_type == "range":
        bounds = [params.get("min"), params.get("max")]
        if all(b is None for b in bounds) or any(b is not None and not _is_number(b) for b in bounds):
            return "params needs a numeric 'min', 'max' or both."
    elif rule_type == "allowed_values":
        if not isinstance(params.get("values"), list) or not params["values"]:
            return "params.values must be a non-empty list."
    elif rule_type == "regex":
        try:
            re.compile(params.get("pattern"))
        except (re.error, TypeError) as exc:
            return f"params.pattern is not a valid regular expression ({exc})."
    elif rule_type == "unique":
        columns = params.get("columns", [])
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            return "params.columns must be a list of column names."
    elif rule_type == "cross_column":
        if not isinstance(params.get("other"), str):
            return "params.other must name the column to compare with."
        if params.get("operator") not in OPERATORS:
            return f"params.operator must be one of {' '.join(OPERATORS)}."
        if params.get("compare_as", "numeric") not in ("numeric", "date"):
            return "params.compare_as must be 'numeric' or 'date'."
    elif rule_type == "outlier":
        if params.get("method", "iqr") not in ("iqr", "zscore"):
            return "params.method must be 'iqr' or 'zscore'."
        for key in ("factor", "threshold"):
            if key in params and not (_is_number(params[key]) and params[key] > 0):
                return f"params.{key} must be a positive number."
    return None


# ---------------------------------------------------------------------- outliers
def outlier_mask(values: pd.Series, method: str = "iqr", factor: float = 1.5,
                 threshold: float = 3.0) -> pd.Series:
    """True where a value is an outlier. Non-numeric and missing values are never flagged.

    iqr:    outside [Q1 - factor * IQR, Q3 + factor * IQR]
    zscore: |value - mean| / std > threshold (sample std; nothing flagged when std is 0)
    """
    numbers = parse_numeric(values)
    present = numbers.dropna()
    none = pd.Series(False, index=values.index)
    if len(present) < 2:
        return none
    if method == "iqr":
        q1, q3 = present.quantile([0.25, 0.75])
        spread = q3 - q1
        return (numbers < q1 - factor * spread) | (numbers > q3 + factor * spread)
    std = present.std()
    if not std or pd.isna(std):
        return none
    return (numbers - present.mean()).abs() / std > threshold


def detect_outliers(df: pd.DataFrame, columns: list[str] | None = None, method: str = "iqr",
                    factor: float = 1.5, threshold: float = 3.0) -> tuple[pd.Series, pd.DataFrame]:
    """Outlier counts per column and a boolean row flag per column. Nothing is removed.

    columns defaults to the columns pandas inferred as numeric; absent columns are skipped.
    """
    if columns is None:
        columns = list(df.select_dtypes(include="number").columns)
    flags = pd.DataFrame(
        {c: outlier_mask(df[c], method, factor, threshold) for c in columns if c in df.columns},
        index=df.index,
    )
    return flags.sum().astype(int), flags


# ---------------------------------------------------------------------- validation
def _expand(rules: list[Rule], columns: list[str]) -> list[Rule]:
    """Resolve a not_null '*' rule with each_column into one rule per data column.

    An explicit not_null rule for a column present in the data takes that column's
    place (its own name and severity) instead of being listed twice.
    """
    per_column = any(r.type == "not_null" and r.column == ALL_COLUMNS and r.params.get("each_column")
                     for r in rules)
    explicit = {r.column: r for r in rules if r.type == "not_null" and r.column != ALL_COLUMNS}
    absorbed = set(explicit) & set(columns) if per_column else set()
    expanded: list[Rule] = []
    for rule in rules:
        if rule.type == "not_null" and rule.column == ALL_COLUMNS:
            expanded.append(rule)
            template = rule.params.get("each_column")
            if template:
                expanded.extend(
                    explicit.get(col) or Rule(name=template.format(column=col), column=col,
                                              type="not_null", severity=rule.severity)
                    for col in columns
                )
        elif not (rule.type == "not_null" and rule.column in absorbed):
            expanded.append(rule)
    return expanded


def _needed_columns(rule: Rule) -> list[str]:
    if rule.type == "unique":
        return list(rule.params.get("columns", []))
    if rule.type == "cross_column":
        return [rule.column, rule.params["other"]]
    return [] if rule.column == ALL_COLUMNS else [rule.column]


def _as_text(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def _parse(series: pd.Series, kind: str) -> pd.Series:
    return parse_dates(series) if kind == "date" else parse_numeric(series)


def _check(df: pd.DataFrame, rule: Rule) -> tuple[int | None, pd.Series]:
    """(count, row flags) for one rule; count is None when a needed column is absent."""
    flags = pd.Series(False, index=df.index)
    if any(c not in df.columns for c in _needed_columns(rule)):
        return None, flags
    p = rule.params
    if rule.type == "not_null" and rule.column == ALL_COLUMNS:
        missing = df.isna()
        return int(missing.to_numpy().sum()), missing.any(axis=1)  # count = empty cells

    raw = df[rule.column] if rule.column != ALL_COLUMNS else None
    if rule.type == "not_null":
        flags = raw.isna()
    elif rule.type == "dtype":
        flags = raw.notna() & _parse(raw, p["kind"]).isna()
    elif rule.type == "range":
        values = parse_numeric(raw)
        if p.get("min") is not None:
            flags |= values < p["min"]
        if p.get("max") is not None:
            flags |= values > p["max"]
    elif rule.type == "allowed_values":
        text = _as_text(raw)
        allowed = {str(v).strip() for v in p["values"]}
        if not p.get("case_sensitive", True):
            text, allowed = text.str.lower(), {v.lower() for v in allowed}
        flags = raw.notna() & ~text.isin(allowed)
    elif rule.type == "regex":
        matches = _as_text(raw).str.fullmatch(p["pattern"]).fillna(False).astype(bool)
        flags = raw.notna() & ~matches
    elif rule.type == "unique":
        flags = df.duplicated(subset=p.get("columns") or None, keep="first")
    elif rule.type == "cross_column":
        kind = p.get("compare_as", "numeric")
        left, right = _parse(raw, kind), _parse(df[p["other"]], kind)
        # Rows where either side is missing or unreadable are left to the other rules.
        flags = left.notna() & right.notna() & ~OPERATORS[p["operator"]](left, right)
    elif rule.type == "outlier":
        flags = outlier_mask(raw, p.get("method", "iqr"), p.get("factor", 1.5), p.get("threshold", 3.0))
    flags = flags.fillna(False).astype(bool)
    return int(flags.sum()), flags


def validate(df: pd.DataFrame, rules: list[Rule]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the rules on df (never modified).

    Returns (summary_df, row_flags_df):
    - summary_df: check, count, status (OK / Issue / Unavailable), severity, details
    - row_flags_df: one boolean column per check (same index as df), True = row failed
    """
    rows = []
    flags: dict[str, pd.Series] = {}
    for rule in _expand(rules, list(df.columns)):
        count, rule_flags = _check(df, rule)
        if count is None:
            status, details = STATUS_UNAVAILABLE, UNAVAILABLE_DETAILS
        else:
            status, details = (STATUS_OK if count == 0 else STATUS_ISSUE), rule.description
        rows.append({"check": rule.name, "count": count, "status": status,
                     "severity": rule.severity, "details": details})
        flags[rule.name] = rule_flags
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary["count"] = summary["count"].astype("Int64")
    return summary, pd.DataFrame(flags, index=df.index)


def quality_score(rules: list[Rule], summary: pd.DataFrame, row_flags: pd.DataFrame) -> QualityScore:
    """Data quality score from the results of validate(df, rules).

    Each dimension is the percentage of rows that fail none of its rules:

        dimension % = 100 * (1 - rows failing at least one rule of the dimension / total rows)

    completeness = not_null rules, validity = dtype / range / allowed_values / regex,
    uniqueness = unique, consistency = cross_column. Outlier rules are not scored.
    Rules marked Unavailable are ignored; a dimension with no usable rule is None (n/a).
    overall = mean of the dimensions that are not None, 0-100 (None if all are None).
    """
    usable = set(summary.loc[summary["status"] != STATUS_UNAVAILABLE, "check"])
    total = len(row_flags)
    scores: dict[str, float | None] = {}
    for dimension in ("completeness", "validity", "uniqueness", "consistency"):
        names = [r.name for r in rules
                 if DIMENSIONS.get(r.type) == dimension and r.name in usable and r.name in row_flags]
        if not names or total == 0:
            scores[dimension] = None
            continue
        failed = int(row_flags[names].any(axis=1).sum())
        scores[dimension] = 100.0 * (1 - failed / total)
    present = [s for s in scores.values() if s is not None]
    return QualityScore(**scores, overall=sum(present) / len(present) if present else None)
