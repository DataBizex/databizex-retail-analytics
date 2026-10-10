"""Which rows failed which quality rule, as a table a user can read and export.

No Qt here. Works on the results of rules.validate(df, rules): the summary table
and the row flags (one boolean column per check, same index as df). The working
copy's index holds the source row numbers (loader.SOURCE_ROW), so they come out
as the first column. Nothing here changes the data.
"""

from __future__ import annotations

import pandas as pd

from app.data.cleaner import STATUS_ISSUE
from app.data.loader import SOURCE_ROW

ALL_FAILED = "All failed rows"  # rows failing at least one error-severity rule
FAILED_RULES = "failed_rules"
FAILED_RULES_SEPARATOR = "; "


def failed_checks(summary: pd.DataFrame, severity: str | None = None) -> list[str]:
    """Checks with a count above zero, in summary order; optionally only one severity."""
    issues = summary[summary["status"] == STATUS_ISSUE]
    if severity is not None:
        issues = issues[issues["severity"] == severity]
    return issues["check"].tolist()


def failed_row_mask(summary: pd.DataFrame, row_flags: pd.DataFrame, selection: str) -> pd.Series:
    """True for rows matching the selection: one check, or ALL_FAILED (error rules only)."""
    checks = failed_checks(summary, "error") if selection == ALL_FAILED else [selection]
    checks = [c for c in checks if c in row_flags.columns]
    if not checks:
        return pd.Series(False, index=row_flags.index)
    return row_flags[checks].any(axis=1)


def failed_rule_choices(summary: pd.DataFrame, row_flags: pd.DataFrame) -> list[tuple[str, str]]:
    """(selection, label) pairs for the dropdown: ALL_FAILED first, then every check with failures."""
    error_rows = int(failed_row_mask(summary, row_flags, ALL_FAILED).sum())
    choices = [(ALL_FAILED, f"{ALL_FAILED} — error rules ({error_rows:,})")]
    counts = summary.set_index("check")["count"]
    for check in failed_checks(summary):
        count, rows = int(counts[check]), int(row_flags[check].sum())
        if count == rows:
            label = f"{check} ({count:,})"
        else:  # "missing values (all columns)" counts empty cells, not rows
            label = f"{check} ({count:,} cells in {rows:,} row{'s' if rows != 1 else ''})"
        choices.append((check, label))
    return choices


def failed_rows(df: pd.DataFrame, summary: pd.DataFrame, row_flags: pd.DataFrame, selection: str,
                limit: int | None = None) -> tuple[pd.DataFrame, int]:
    """(table, total matching rows). Table columns: source_row, the data columns, failed_rules.

    failed_rules lists every check the row failed (any severity), separated by "; ".
    limit caps the rows returned (for display); the total is always the full count.
    """
    mask = failed_row_mask(summary, row_flags, selection)
    total = int(mask.sum())
    index = mask.index[mask]
    if limit is not None:
        index = index[:limit]
    flagged = row_flags.loc[index, [c for c in failed_checks(summary) if c in row_flags.columns]]
    names = flagged.columns.to_numpy()
    table = df.loc[index].copy()
    # A re-imported failed-rows export already has these columns; keep them and add ours beside.
    row_col = SOURCE_ROW if SOURCE_ROW not in table.columns else f"{SOURCE_ROW} (this file)"
    rules_col = FAILED_RULES if FAILED_RULES not in table.columns else f"{FAILED_RULES} (this file)"
    table.insert(0, row_col, index.to_numpy())
    table[rules_col] = [FAILED_RULES_SEPARATOR.join(names[row]) for row in flagged.to_numpy()]
    return table.reset_index(drop=True), total
