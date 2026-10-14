"""The change log: every fix applied to the working copy, with undo. No Qt here.

Each ChangeEntry records what a fix did (rule, action, columns, parameters, counts,
affected source rows and a before/after sample) and the quality score and KPI-valid
row count before and after. ChangeLog keeps the entries in order and a bounded stack
of snapshots of the state before each fix, so the most recent fixes can be undone
exactly. The log lives in memory for one session; it is not saved between sessions.
"""

from __future__ import annotations

import io
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

SOURCE_ROWS_CAP = 5000  # source_row values stored per entry; the full count is always kept
SNAPSHOT_LIMIT = 10  # fixes that can be undone; older ones need "Reset working copy to original"
SAMPLE_LIMIT = 20

KIND_FIX = "fix"
KIND_NORMALIZATION = "normalization"
CSV_ENCODING = "utf-8-sig"  # same convention as the exporter (BOM so Excel detects UTF-8)


@dataclass
class ChangeEntry:
    rule: str
    action: str
    columns: list[str]
    parameters: dict
    rows_affected: int
    cells_changed: int
    source_rows: list
    source_rows_total: int
    truncated: bool
    samples: list[dict]  # {"source_row", "column", "before", "after"}, at most SAMPLE_LIMIT
    kind: str = KIND_FIX
    id: int = 0  # set by ChangeLog.add
    timestamp: str = ""  # ISO, local time; set by ChangeLog.add
    quality_score_before: float | None = None
    quality_score_after: float | None = None
    valid_rows_before: int | None = None
    valid_rows_after: int | None = None


def capped_rows(rows) -> tuple[list, int, bool]:
    """(stored source rows, full count, truncated) for a list of affected source rows."""
    rows = list(rows)
    return rows[:SOURCE_ROWS_CAP], len(rows), len(rows) > SOURCE_ROWS_CAP


def _score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _rows(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


@dataclass
class ChangeLog:
    entries: list[ChangeEntry] = field(default_factory=list)
    snapshot_limit: int = SNAPSHOT_LIMIT
    # (entry id, state before that entry); the state is whatever the caller needs to restore.
    _snapshots: deque = field(default_factory=deque)
    # Steps that define the starting point (import options, column mapping); not undoable.
    preparation: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, entry: ChangeEntry, state_before) -> ChangeEntry:
        """Number, time-stamp and store entry; state_before is what undo_last() hands back."""
        entry.id = self.entries[-1].id + 1 if self.entries else 1
        entry.timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        self.entries.append(entry)
        self._snapshots.append((entry.id, state_before))
        while len(self._snapshots) > self.snapshot_limit:
            self._snapshots.popleft()
        return entry

    def can_undo(self) -> bool:
        return bool(self.entries) and bool(self._snapshots) and self._snapshots[-1][0] == self.entries[-1].id

    def undoable_count(self) -> int:
        return len(self._snapshots)

    def undo_last(self):
        """(entry, state before it) for the most recent entry, removed from the log; None if not possible."""
        if not self.can_undo():
            return None
        _id, state = self._snapshots.pop()
        return self.entries.pop(), state

    def clear(self) -> None:
        self.entries.clear()
        self._snapshots.clear()
        self.preparation = []

    # ------------------------------------------------------------------ exports
    def to_dataframe(self) -> pd.DataFrame:
        rows = [{
            "id": e.id,
            "time": e.timestamp,
            "kind": e.kind,
            "rule": e.rule,
            "action": e.action,
            "columns": ", ".join(e.columns),
            "parameters": json.dumps(e.parameters, ensure_ascii=False, sort_keys=True, default=str),
            "rows_affected": e.rows_affected,
            "cells_changed": e.cells_changed,
            "source_rows": ";".join(str(r) for r in e.source_rows),
            "source_rows_total": e.source_rows_total,
            "source_rows_truncated": e.truncated,
            "quality_score_before": e.quality_score_before,
            "quality_score_after": e.quality_score_after,
            "valid_rows_before": e.valid_rows_before,
            "valid_rows_after": e.valid_rows_after,
        } for e in self.entries]
        columns = ["id", "time", "kind", "rule", "action", "columns", "parameters", "rows_affected",
                   "cells_changed", "source_rows", "source_rows_total", "source_rows_truncated",
                   "quality_score_before", "quality_score_after", "valid_rows_before", "valid_rows_after"]
        return pd.DataFrame(rows, columns=columns)

    def to_csv_text(self) -> str:
        buffer = io.StringIO()
        self.to_dataframe().to_csv(buffer, index=False, lineterminator="\n")
        return buffer.getvalue()

    def to_markdown(self, source_name: str = "", generated: datetime | None = None) -> str:
        """A readable audit report: summary table, then one section per entry."""
        generated = generated or datetime.now()
        lines = [
            "# DataBizex change log",
            "",
            f"- Source file: {source_name or '(unknown)'} (never modified)",
            f"- Generated: {generated:%Y-%m-%d %H:%M}",
            f"- Changes logged: {len(self.entries)}",
        ]
        if self.preparation:
            lines.append(f"- Preparation (before any fix): {'; '.join(self.preparation)}")
        lines += ["", "All changes apply to the working copy only. The original import and the source file "
                  "are unchanged.", ""]
        if not self.entries:
            lines.append("No fixes applied.")
            return "\n".join(lines) + "\n"
        lines += ["## Summary", "",
                  "| # | Time | Rule | Action | Rows | Cells | Quality score | KPI-valid rows |",
                  "|---|---|---|---|---:|---:|---|---|"]
        for e in self.entries:
            lines.append(f"| {e.id} | {e.timestamp} | {_md(e.rule)} | {e.action} | {e.rows_affected:,} | "
                         f"{e.cells_changed:,} | {_score(e.quality_score_before)} -> {_score(e.quality_score_after)} | "
                         f"{_rows(e.valid_rows_before)} -> {_rows(e.valid_rows_after)} |")
        for e in self.entries:
            lines += ["", f"## {e.id}. {_md(e.rule)}: {e.action}", "",
                      f"- Time: {e.timestamp}",
                      f"- Kind: {e.kind}",
                      f"- Columns: {', '.join(e.columns) or '(whole row)'}",
                      f"- Rows affected: {e.rows_affected:,}",
                      f"- Cells changed: {e.cells_changed:,}",
                      f"- Quality score: {_score(e.quality_score_before)} -> {_score(e.quality_score_after)}",
                      f"- KPI-valid rows: {_rows(e.valid_rows_before)} -> {_rows(e.valid_rows_after)}"]
            if e.parameters:
                lines.append("- Parameters: " + json.dumps(e.parameters, ensure_ascii=False, sort_keys=True,
                                                           default=str))
            if e.source_rows:
                shown = ", ".join(str(r) for r in e.source_rows[:50])
                more = f" ... ({e.source_rows_total:,} in total)" if e.source_rows_total > 50 else ""
                lines.append(f"- Source rows: {shown}{more}")
            if e.truncated:
                lines.append(f"- Note: only the first {len(e.source_rows):,} of {e.source_rows_total:,} "
                             "source rows are stored.")
            if e.samples:
                lines += ["", "| source_row | column | before | after |", "|---:|---|---|---|"]
                lines += [f"| {s['source_row']} | {_md(s['column'])} | {_md(s['before'])} | {_md(s['after'])} |"
                          for s in e.samples]
        return "\n".join(lines) + "\n"


def _md(value) -> str:
    """Text safe inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")
