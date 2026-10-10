"""Load CSV / Excel sales exports into a pandas DataFrame.

The source file is only ever opened for reading.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}
AUTO_ENCODINGS = ("utf-8-sig", "cp1252")  # UTF-8 (with or without BOM), then the common Windows code page
SOURCE_ROW = "source_row"  # index name of the working copy: line number in the source file
FILE_DIALOG_FILTER = "Sales data (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls)"


class DataLoadError(Exception):
    """Raised with a user-readable message when a file cannot be loaded."""


@dataclass
class LoadResult:
    df: pd.DataFrame
    path: Path
    warnings: list[str] = field(default_factory=list)
    # How a CSV was read (None for Excel), so the import settings can be shown and changed.
    delimiter: str | None = None
    encoding: str | None = None
    # Line number in the source file of each row of df (header = line 1); Excel: sheet row.
    source_rows: list[int] = field(default_factory=list)


def with_source_rows(df: pd.DataFrame, source_rows: list[int]) -> pd.DataFrame:
    """A copy of df indexed by source line number (index name SOURCE_ROW)."""
    return df.set_axis(pd.Index(source_rows, name=SOURCE_ROW), axis=0).copy()


def load_file(path: str | Path, delimiter: str | None = None, encoding: str | None = None) -> LoadResult:
    """Read the first sheet / the CSV. delimiter and encoding are detected unless given."""
    path = Path(path)
    ext = path.suffix.lower()

    if ext == ".xls":
        raise DataLoadError(
            "Legacy .xls files are not supported (it would need the extra 'xlrd' library). "
            "Open the file in Excel and save it as .xlsx or .csv, then import it again."
        )
    if ext not in SUPPORTED_EXTENSIONS:
        raise DataLoadError(f"Unsupported file type '{ext or '(none)'}'. Use .csv or .xlsx.")
    if not path.is_file():
        raise DataLoadError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise DataLoadError("The file is empty (0 bytes).")

    try:
        if ext == ".csv":
            df, encoding, delimiter = _read_csv(path, encoding=encoding, delimiter=delimiter)
        else:
            df, encoding, delimiter = _read_excel(path), None, None
    except DataLoadError:
        raise
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, ValueError, OSError) as exc:
        logger.exception("Failed to read %s", path)
        raise DataLoadError(f"Could not read the file: {exc}") from exc

    header_row: int | None = 0
    warnings: list[str] = []
    if _looks_headerless(df):
        warnings.append(
            "The first row looks like data, not column names. "
            "The file was loaded without a header and columns were named column_1, column_2, ..."
        )
        header_row = None
        if ext == ".csv":
            df = _read_csv(path, header=None, encoding=encoding, delimiter=delimiter)[0]
        else:
            df = _read_excel(path, header=None)
        df.columns = [f"column_{i + 1}" for i in range(df.shape[1])]

    if ext == ".csv":
        lines = _csv_record_lines(path, encoding, delimiter, header_row, len(df))
    else:
        first = 1 if header_row is None else 2  # pandas keeps blank sheet rows, header is row 1
        lines = list(range(first, first + len(df)))
    df = df.dropna(axis=0, how="all")  # fully blank lines are formatting, not data
    source_rows = [lines[i] for i in df.index]
    if df.shape[1] == 0:
        raise DataLoadError("No columns were found in the file.")
    if df.empty:
        raise DataLoadError("The file has column names but no data rows.")

    df.columns = [str(c).strip() for c in df.columns]
    df = df.reset_index(drop=True)
    logger.info("Loaded %s: %d rows x %d columns (delimiter %r, encoding %s)",
                path, *df.shape, delimiter, encoding)
    return LoadResult(df=df, path=path, warnings=warnings, delimiter=delimiter, encoding=encoding,
                      source_rows=source_rows)


def _sniff_delimiter(path: Path, encoding: str) -> str:
    with path.open("r", encoding=encoding, newline="") as fh:
        sample = fh.read(64 * 1024)
    if not sample.strip():
        raise DataLoadError("The file contains no data.")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv(path: Path, header: int | None = 0, encoding: str | None = None,
              delimiter: str | None = None) -> tuple[pd.DataFrame, str, str]:
    """(DataFrame, encoding used, delimiter used). Without an encoding, AUTO_ENCODINGS are tried in order."""
    last_error: Exception | None = None
    for enc in (encoding,) if encoding else AUTO_ENCODINGS:
        try:
            sep = delimiter or _sniff_delimiter(path, enc)
            df = pd.read_csv(path, sep=sep, encoding=enc, header=header, skipinitialspace=True)
            return df, enc, sep
        except UnicodeDecodeError as exc:
            last_error = exc
    if encoding:
        raise DataLoadError(f"Could not decode the file as {encoding} text: {last_error}")
    raise DataLoadError(f"Could not decode the file as UTF-8 or Windows-1252 text: {last_error}")


def _csv_record_lines(path: Path, encoding: str, delimiter: str, header: int | None,
                      expected: int) -> list[int]:
    """Starting line number of each data record, matching the rows pandas returns.

    Like pandas, empty and whitespace-only lines are skipped; a quoted value with a
    line break makes a record span several lines. Falls back to counting rows after
    the header if the two readers ever disagree.
    """
    starts: list[int] = []
    try:
        with path.open("r", encoding=encoding, newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            previous_end = 0
            for record in reader:
                if record and not (len(record) == 1 and not record[0].strip()):
                    starts.append(previous_end + 1)
                previous_end = reader.line_num
    except (csv.Error, OSError, UnicodeDecodeError) as exc:
        logger.warning("Could not number the lines of %s: %s", path, exc)
        starts = []
    data = starts[1:] if header == 0 else starts
    if len(data) != expected:
        logger.warning("Line numbering of %s found %d records, pandas read %d; numbering rows instead",
                       path, len(data), expected)
        first = 1 if header is None else 2
        return list(range(first, first + expected))
    return data


def _read_excel(path: Path, header: int | None = 0) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=0, header=header, engine="openpyxl")
    except Exception as exc:  # openpyxl raises several unrelated exception types
        raise DataLoadError(f"Could not read the Excel file (first sheet): {exc}") from exc


def _looks_like_value(name: object) -> bool:
    if not isinstance(name, str):
        return True  # numeric or datetime header cells
    text = name.strip()
    if text == "" or text.startswith("Unnamed:"):
        return False
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        pass
    return bool(pd.notna(pd.to_datetime(text, errors="coerce", format="ISO8601")))


def _looks_headerless(df: pd.DataFrame) -> bool:
    """True when at least half of the header cells look like data values."""
    names = list(df.columns)
    if not names:
        return False
    value_like = sum(_looks_like_value(n) for n in names)
    return value_like >= max(1, len(names) / 2)
