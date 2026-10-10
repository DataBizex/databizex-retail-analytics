"""Find and merge text values that differ only in case or spacing.

" germany", "GERMANY", "Germany " and "Germany" are the same value written
differently: after trimming, collapsing internal whitespace and ignoring case
they are identical, so they form one group. The group is written in one canonical
form: always trimmed, with single spaces, in the letter case of the most frequent
variant ("Germany").

A value with stray spaces ("Spain ", "New  York") needs normalizing even when it is
the only spelling: it forms a one-member group whose canonical form is the cleaned
value. A lone value without a spacing problem is left alone, since a case difference
needs a second spelling to compare against.

There is NO fuzzy matching. Values that are spelled differently are never
merged: "Deutschland" and "Germany", or "Grmany" and "Germany", stay separate.

No Qt here. apply_normalization returns a NEW DataFrame (index, and so the
source row numbers, kept); the input is never changed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from app.data.cleaner import parse_numeric
from app.data.profiler import NUMERIC_SHARE

logger = logging.getLogger(__name__)

MAX_DISTINCT = 200        # categorical-looking: at most this many distinct values ...
MAX_DISTINCT_SHARE = 0.5  # ... and fewer distinct values than this share of the rows
IDENTIFIER_COLUMNS = ("order_id",)  # never normalized (the mapped order ID)

_WHITESPACE = re.compile(r"\s+")


@dataclass
class VariantGroup:
    canonical: str
    variants: list[tuple[str, int]]  # (value as written, rows), most frequent first

    @property
    def changes(self) -> int:
        """Rows whose value would change to the canonical form."""
        return sum(count for value, count in self.variants if value != self.canonical)

    @property
    def whitespace_only(self) -> bool:
        """True when the variants differ from the canonical form only in spacing, not case."""
        return all(clean_spacing(value) == self.canonical for value, _count in self.variants)


def clean_spacing(value: str) -> str:
    """Leading and trailing whitespace removed, internal whitespace collapsed to single spaces."""
    return _WHITESPACE.sub(" ", value.strip())


def variant_key(value: str) -> str:
    """Trimmed, internal whitespace collapsed, case ignored."""
    return clean_spacing(value).casefold()


def _is_text(series: pd.Series) -> bool:
    """Text column that is not mostly numbers (a few junk values make pandas read numbers as text)."""
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return False
    present = series.dropna()
    return present.empty or parse_numeric(present).notna().mean() < NUMERIC_SHARE


def categorical_columns(df: pd.DataFrame) -> list[str]:
    """Text columns that look categorical and are not identifiers."""
    columns = []
    for col in df.columns:
        series = df[col]
        if col in IDENTIFIER_COLUMNS or not _is_text(series):
            continue
        present = series.dropna()
        distinct = present.nunique()
        if distinct == 0 or distinct == len(present):  # empty, or every value unique: an identifier
            continue
        if distinct <= MAX_DISTINCT and distinct < MAX_DISTINCT_SHARE * len(df):
            columns.append(col)
    return columns


def variant_groups(series: pd.Series) -> list[VariantGroup]:
    """Groups of values to normalize, largest change first.

    A group has more than one way of writing the same value, or is a single value with
    stray spaces (one-member group, canonical form = the cleaned value).

    Canonical form: the most frequent variant, trimmed and with internal whitespace
    collapsed (clean_spacing), so it never has stray spaces. Its letter case comes from
    that variant; on a tie in frequency the alphabetically first trimmed form wins.
    """
    counts = series[series.map(lambda v: isinstance(v, str))].value_counts()
    by_key: dict[str, list[tuple[str, int]]] = {}
    for value, count in counts.items():
        by_key.setdefault(variant_key(value), []).append((value, int(count)))
    groups = []
    for variants in by_key.values():
        if len(variants) < 2 and clean_spacing(variants[0][0]) == variants[0][0]:
            continue  # a lone, clean value: nothing to compare its case against
        variants.sort(key=lambda vc: (-vc[1], clean_spacing(vc[0]), vc[0]))
        groups.append(VariantGroup(canonical=clean_spacing(variants[0][0]), variants=variants))
    groups.sort(key=lambda g: (-g.changes, g.canonical))
    return groups


def variant_mapping(series: pd.Series) -> dict[str, str]:
    """{value as written: canonical form} for every value that would change."""
    return {value: group.canonical
            for group in variant_groups(series)
            for value, _count in group.variants if value != group.canonical}


def find_text_variants(df: pd.DataFrame) -> dict[str, list[VariantGroup]]:
    """Per categorical-looking text column, the groups it would merge (columns without any are left out)."""
    found = {col: groups for col in categorical_columns(df) if (groups := variant_groups(df[col]))}
    if found:
        logger.info("Text variants found in: %s",
                    ", ".join(f"{c} ({sum(g.changes for g in gs)} values)" for c, gs in found.items()))
    return found


def apply_normalization(df: pd.DataFrame, selected: Iterable[str]) -> pd.DataFrame:
    """A copy of df with the variants in the selected columns replaced by their canonical form.

    Only categorical-looking text columns are changed, so identifier columns are never
    normalized even if selected. Other columns, the index (source row numbers) and df
    itself are unchanged.
    """
    out = df.copy()
    eligible = set(categorical_columns(df))
    for col in selected:
        if col not in eligible:
            continue
        mapping = variant_mapping(out[col])
        if mapping:
            changed = out[col].isin(list(mapping))
            out[col] = out[col].where(~changed, out[col].map(mapping))
            logger.info("Normalized %d values in %s", int(changed.sum()), col)
    return out
