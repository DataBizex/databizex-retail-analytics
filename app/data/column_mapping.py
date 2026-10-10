"""Map a file's own column names onto the columns DataBizex expects.

Pure pandas (no Qt) so the mapping rules can be tested on their own. The
original DataFrame is never modified; apply_column_mapping returns a new one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS = [
    "order_id", "order_date", "country", "product", "category",
    "quantity", "unit_price", "discount_pct", "unit_cost",
]
# The dialog is only offered when one of these is missing by exact name.
MAPPING_TRIGGER_COLUMNS = ["quantity", "unit_price", "discount_pct", "unit_cost", "order_date"]
NOT_AVAILABLE_LABEL = "(not available)"

# Case-insensitive substrings tried in order for each expected column. Fields are
# guessed in this dict's order and a source column is used at most once, so the
# more specific fields (price, cost, ...) claim their columns before order_id / product.
GUESS_KEYWORDS: dict[str, list[str]] = {
    "unit_price": ["price"],
    "unit_cost": ["cost"],
    "discount_pct": ["discount"],
    "quantity": ["qty", "quantity", "frequency"],
    "order_date": ["date"],
    "category": ["category"],
    "country": ["country"],
    "order_id": ["id"],
    "product": ["brand", "product"],
}

DISCOUNT_FILLED_NOTE = "discount_pct was not available in the file and was set to 0 for every row."
COST_UNAVAILABLE_NOTE = "unit_cost was not available: gross profit and gross margin are shown as n/a."
DATE_UNAVAILABLE_NOTE = "order_date was not available: the monthly trend is shown as n/a."


class ColumnMappingError(Exception):
    """Raised with a user-readable message when a mapping cannot be applied."""


@dataclass
class MappingResult:
    df: pd.DataFrame
    # Expected columns with no source column (discount_pct excluded: it is filled with 0).
    unavailable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def needs_mapping(df: pd.DataFrame) -> bool:
    return any(c not in df.columns for c in MAPPING_TRIGGER_COLUMNS)


def guess_mapping(columns: list[str]) -> dict[str, str | None]:
    """Best guess {expected column: source column or None}.

    An exact (case-insensitive) name match wins; otherwise the first unused
    source column containing one of the field's keywords.
    """
    columns = [str(c) for c in columns]
    by_lower = {c.lower(): c for c in columns}
    guess: dict[str, str | None] = {}
    used: set[str] = set()
    for target in EXPECTED_COLUMNS:
        exact = by_lower.get(target)
        if exact is not None:
            guess[target] = exact
            used.add(exact)
    for target, keywords in GUESS_KEYWORDS.items():
        if target in guess:
            continue
        guess[target] = None
        for keyword in keywords:
            match = next((c for c in columns if c not in used and keyword in c.lower()), None)
            if match is not None:
                guess[target] = match
                used.add(match)
                break
    return {target: guess[target] for target in EXPECTED_COLUMNS}


def mapping_problems(columns: list[str], mapping: dict[str, str | None]) -> list[str]:
    """User-readable reasons the mapping cannot be applied (empty when it is fine)."""
    problems = []
    chosen = {target: source for target, source in mapping.items() if source is not None}
    for source in sorted(set(chosen.values())):
        targets = [t for t, s in chosen.items() if s == source]
        if source not in columns:
            problems.append(f"Column '{source}' is not in the file.")
        elif len(targets) > 1:
            problems.append(f"Column '{source}' is chosen for more than one field: {', '.join(targets)}.")
    renamed = set(chosen.values())
    for target, source in chosen.items():
        if target != source and target in columns and target not in renamed:
            problems.append(
                f"The file already has a column named '{target}'. "
                f"Choose that column for {target}, or pick '{NOT_AVAILABLE_LABEL}'."
            )
    return problems


def apply_column_mapping(df: pd.DataFrame, mapping: dict[str, str | None]) -> MappingResult:
    """Rename a copy of df according to mapping; unmapped source columns are kept as they are."""
    problems = mapping_problems(list(df.columns), mapping)
    if problems:
        raise ColumnMappingError("\n".join(problems))

    renames = {source: target for target, source in mapping.items()
               if source is not None and source != target}
    out = df.rename(columns=renames)  # returns a new DataFrame
    unavailable: list[str] = []
    notes: list[str] = []
    for target in EXPECTED_COLUMNS:
        if mapping.get(target) is not None:
            continue
        if target == "discount_pct":
            out["discount_pct"] = 0.0
            notes.append(DISCOUNT_FILLED_NOTE)
            continue
        unavailable.append(target)
    if "unit_cost" in unavailable:
        notes.append(COST_UNAVAILABLE_NOTE)
    if "order_date" in unavailable:
        notes.append(DATE_UNAVAILABLE_NOTE)
    logger.info("Applied column mapping %s; unavailable: %s", renames, unavailable or "none")
    return MappingResult(df=out, unavailable=unavailable, notes=notes)
