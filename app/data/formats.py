"""Detect and apply regional number and date formats (e.g. "1.234,56", "10.04.2024").

No Qt here, so detection and conversion can be tested on their own. Conversion
returns a NEW DataFrame for the working copy; the original import is never changed.

Converted values become real numbers / ISO "YYYY-MM-DD" text, so the existing
parsers (cleaner.parse_numeric / parse_dates), KPIs and quality rules work
unchanged. Values that do not fit the chosen format keep their original text,
so the quality checks still report them as invalid instead of hiding them.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

ISO_DATE = "%Y-%m-%d"
DEFAULT_ENCODING = "utf-8-sig"  # what the loader tries first; reads plain UTF-8 too
DELIMITERS = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
ENCODINGS = {"utf-8-sig": "UTF-8", "cp1252": "Windows-1252", "latin-1": "ISO-8859-1"}
DECIMALS = {".": "dot", ",": "comma"}
THOUSANDS = {"": "none", ".": "dot", ",": "comma", " ": "space", "'": "apostrophe"}
DATE_FORMATS = {ISO_DATE: "YYYY-MM-DD", "%d.%m.%Y": "DD.MM.YYYY", "%d/%m/%Y": "DD/MM/YYYY",
                "%m/%d/%Y": "MM/DD/YYYY"}

# A column is converted when at least this share of its non-empty values fits the format.
MIN_SHARE = 0.8
SAMPLE_SIZE = 500

_COMMA_DECIMAL = re.compile(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,3}")
_DOT_THOUSANDS = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?")
_COMMA_THOUSANDS = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_DATE_PATTERNS = {
    ISO_DATE: re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),
    "%d.%m.%Y": re.compile(r"\d{1,2}\.\d{1,2}\.\d{4}"),
    "slash": re.compile(r"\d{1,2}/\d{1,2}/\d{4}"),
}


@dataclass(frozen=True)
class ImportOptions:
    """How the file is read and how its numbers and dates are interpreted.

    delimiter / encoding are None for Excel files.
    """
    delimiter: str | None = ","
    encoding: str | None = DEFAULT_ENCODING
    decimal: str = "."
    thousands: str = ""
    date_format: str = ISO_DATE

    def is_default(self) -> bool:
        return (self.delimiter in (",", None) and self.encoding in (DEFAULT_ENCODING, None)
                and self.decimal == "." and self.thousands == "" and self.date_format == ISO_DATE)

    def describe(self) -> str:
        parts = []
        if self.delimiter is not None:
            parts.append(f"{DELIMITERS.get(self.delimiter, repr(self.delimiter))} delimiter")
        if self.encoding is not None:
            parts.append(ENCODINGS.get(self.encoding, self.encoding))
        parts.append(f"decimal {DECIMALS[self.decimal]}")
        parts.append(f"thousands separator {THOUSANDS[self.thousands]}")
        parts.append(f"dates {DATE_FORMATS[self.date_format]}")
        return ", ".join(parts)


def options_problem(options: ImportOptions) -> str | None:
    if options.thousands and options.thousands == options.decimal:
        return "The decimal and thousands separators must be different."
    return None


@dataclass
class FormatDetection:
    options: ImportOptions                      # the detected settings
    decimal_columns: list[str] = field(default_factory=list)
    date_columns: dict[str, str] = field(default_factory=dict)  # column -> date format
    ambiguous_date_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def is_default(self) -> bool:
        return self.options.is_default() and not self.ambiguous_date_columns


@dataclass
class ConversionResult:
    df: pd.DataFrame
    numeric_columns: list[str] = field(default_factory=list)
    date_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- helpers
def _text_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])]


def _present_text(series: pd.Series) -> pd.Series:
    """Stripped text of the non-empty values."""
    text = series.dropna().astype("string").str.strip()
    return text[text != ""]


def _number_pattern(decimal: str, thousands: str) -> str:
    whole = rf"\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+|\d+" if thousands else r"\d+"
    return rf"[+-]?(?:{whole})(?:{re.escape(decimal)}\d+)?"


def parse_localized_numbers(text: pd.Series, decimal: str, thousands: str = "") -> pd.Series:
    """Numbers from text such as "1.234,56" (decimal ",", thousands "."); NaN where it does not fit."""
    text = text.astype("string").str.strip()
    fits = text.str.fullmatch(_number_pattern(decimal, thousands)).fillna(False).astype(bool)
    cleaned = text.where(fits)
    if thousands:
        cleaned = cleaned.str.replace(thousands, "", regex=False)
    cleaned = cleaned.str.replace(decimal, ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce").astype(float)


def _slash_order(text: pd.Series) -> tuple[str, bool, bool]:
    """(format, ambiguous, mixed) for D/M/Y vs M/D/Y values; DD/MM is preferred when unclear."""
    parts = text.str.extract(r"^(\d{1,2})/(\d{1,2})/\d{4}$").dropna().astype(int)
    first_over_12 = bool((parts[0] > 12).any())
    second_over_12 = bool((parts[1] > 12).any())
    if second_over_12 and not first_over_12:
        return "%m/%d/%Y", False, False
    return "%d/%m/%Y", not (first_over_12 or second_over_12), first_over_12 and second_over_12


# ---------------------------------------------------------------------- detection
def detect_formats(df: pd.DataFrame, delimiter: str | None = ",",
                   encoding: str | None = DEFAULT_ENCODING) -> FormatDetection:
    """Detect decimal/thousands separators and date formats per text column.

    delimiter / encoding are what the loader used (None for Excel); they are
    recorded so the detection describes the whole import.
    """
    comma_cols, dot_thousands, us_cols = [], False, []
    date_columns: dict[str, str] = {}
    ambiguous: list[str] = []
    notes: list[str] = []
    for col in _text_columns(df):
        sample = _present_text(df[col]).head(SAMPLE_SIZE)
        if sample.empty:
            continue
        date_format = _detect_date_format(sample)
        if date_format is not None:
            fmt, is_ambiguous, mixed = date_format
            date_columns[col] = fmt
            if is_ambiguous:
                ambiguous.append(col)
                notes.append(f"{col}: day/month order is ambiguous (no day above 12); read as DD/MM.")
            if mixed:
                notes.append(f"{col}: values use both DD/MM and MM/DD order; read as DD/MM.")
            continue
        if (sample.str.fullmatch(_COMMA_DECIMAL).any()
                and sample.str.fullmatch(_number_pattern(",", ".")).mean() >= MIN_SHARE):
            comma_cols.append(col)
            dot_thousands = dot_thousands or bool(sample.str.fullmatch(_DOT_THOUSANDS).any())
        elif (sample.str.fullmatch(_COMMA_THOUSANDS).any()
              and sample.str.fullmatch(_number_pattern(".", ",")).mean() >= MIN_SHARE):
            us_cols.append(col)

    if comma_cols:
        decimal, thousands = ",", "." if dot_thousands else ""
        if us_cols:
            notes.append("Some columns use decimal commas and others use thousands commas; "
                         f"decimal comma chosen. Check: {', '.join(us_cols)}.")
    elif us_cols:
        decimal, thousands = ".", ","
    else:
        decimal, thousands = ".", ""
    non_iso = Counter(f for f in date_columns.values() if f != ISO_DATE)
    date_format = non_iso.most_common(1)[0][0] if non_iso else ISO_DATE
    if len(non_iso) > 1:
        notes.append("Date columns use different formats; one format is applied to all of them.")

    options = ImportOptions(delimiter, encoding, decimal, thousands, date_format)
    detection = FormatDetection(options, comma_cols or us_cols, date_columns, ambiguous, notes)
    logger.info("Detected import format: %s; number columns %s; date columns %s",
                options.describe(), detection.decimal_columns or "none", date_columns or "none")
    return detection


def _detect_date_format(sample: pd.Series) -> tuple[str, bool, bool] | None:
    """(format, ambiguous, mixed) when most values look like one date style, else None."""
    for key, pattern in _DATE_PATTERNS.items():
        if sample.str.fullmatch(pattern).mean() >= MIN_SHARE:
            if key == "slash":
                return _slash_order(sample)
            return key, False, False
    return None


# ---------------------------------------------------------------------- conversion
def apply_import_options(df: pd.DataFrame, options: ImportOptions) -> ConversionResult:
    """Return a NEW DataFrame with the chosen number and date formats converted.

    A text column is converted when at least MIN_SHARE of its non-empty values
    fit the format (numbers additionally need at least one value that actually
    uses the decimal or thousands character, so codes such as "01067" are left
    alone). Values that do not fit keep their original text.
    """
    problem = options_problem(options)
    if problem:
        raise ValueError(problem)
    out = df.copy()
    result = ConversionResult(df=out)

    for col in _text_columns(out):
        present = out[col].notna() & (out[col].astype("string").str.strip() != "")
        if not present.any():
            continue
        text = out[col].astype("string").str.strip()

        if options.date_format != ISO_DATE:
            dates = pd.to_datetime(text, format=options.date_format, errors="coerce")
            if dates[present].notna().mean() >= MIN_SHARE:
                out[col] = _merge(out[col], dates.dt.strftime(ISO_DATE), dates.notna())
                result.date_columns.append(col)
                if options.date_format in ("%d/%m/%Y", "%m/%d/%Y") and _slash_order(text[present])[1]:
                    result.notes.append(f"{col}: day/month order is ambiguous; read as "
                                        f"{DATE_FORMATS[options.date_format]}.")
                continue

        special = ({options.decimal} - {"."}) | ({options.thousands} if options.thousands else set())
        if not special:
            continue
        numbers = parse_localized_numbers(text, options.decimal, options.thousands)
        uses_format = text[numbers.notna()].str.contains("|".join(map(re.escape, special))).any()
        if uses_format and numbers[present].notna().mean() >= MIN_SHARE:
            out[col] = _merge(out[col], numbers, numbers.notna())
            result.numeric_columns.append(col)

    if result.numeric_columns or result.date_columns:
        logger.info("Converted with %s: numbers %s, dates %s", options.describe(),
                    result.numeric_columns or "none", result.date_columns or "none")
    return result


def _merge(original: pd.Series, converted: pd.Series, ok: pd.Series) -> pd.Series:
    """Converted values where ok, original text elsewhere; a plain typed column if all converted."""
    if bool(ok[original.notna()].all()):
        return converted.where(ok)
    return original.astype(object).where(~ok, converted.astype(object))


def import_summary(options: ImportOptions, conversion: ConversionResult | None) -> str:
    """One line for the source label and package_info.txt: settings, converted columns, notes."""
    parts = [options.describe()]
    if conversion is not None:
        if conversion.numeric_columns:
            parts.append("converted to numbers: " + ", ".join(conversion.numeric_columns))
        if conversion.date_columns:
            parts.append("converted to ISO dates: " + ", ".join(conversion.date_columns))
        parts.extend(conversion.notes)
    return "; ".join(parts)
