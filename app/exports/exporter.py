"""Exports: CSV / Excel files and the Power BI handoff package.

Every function writes only to the path or folder the user chose and raises
ExportError with a readable message when writing fails.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Collection
from datetime import datetime
from pathlib import Path

import pandas as pd

from app import __version__
from app.analytics.kpis import prepare_sales_frame, validity_summary
from app.paths import resource_path

logger = logging.getLogger(__name__)

CSV_ENCODING = "utf-8-sig"  # BOM so Excel detects UTF-8 correctly
POWERBI_CSV_NAME = "sales_clean.csv"
POWERBI_DOCS = ["POWER_BI_GUIDE.md", "data_dictionary.md"]
POWERBI_COLUMNS = [
    "order_id", "order_date", "year", "month", "country", "product", "category",
    "quantity", "unit_price", "discount_pct", "unit_cost", "net_revenue", "gross_profit",
]


class ExportError(Exception):
    """Raised with a user-readable message when an export fails."""


def _friendly(exc: Exception, path: Path) -> ExportError:
    if isinstance(exc, PermissionError):
        return ExportError(f"Permission denied writing '{path}'. "
                           "Close the file if it is open in Excel, or choose another folder.")
    if isinstance(exc, FileNotFoundError):
        return ExportError(f"The folder for '{path}' does not exist.")
    return ExportError(f"Could not write '{path}': {exc}")


def export_csv(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    try:
        df.to_csv(path, index=False, encoding=CSV_ENCODING)
    except OSError as exc:
        logger.exception("CSV export failed")
        raise _friendly(exc, path) from exc
    logger.info("Exported %d rows to %s", len(df), path)
    return path


def export_excel(df: pd.DataFrame, path: str | Path, sheet_name: str = "data") -> Path:
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    except OSError as exc:
        logger.exception("Excel export failed")
        raise _friendly(exc, path) from exc
    logger.info("Exported %d rows to %s", len(df), path)
    return path


def export_tables_csv(tables: dict[str, pd.DataFrame], folder: str | Path) -> list[Path]:
    """Write each table to <folder>/<name>.csv. Returns the written paths."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ExportError(f"Folder does not exist: {folder}")
    return [export_csv(df, folder / f"{name}.csv") for name, df in tables.items()]


def powerbi_frame(working: pd.DataFrame, unavailable: Collection[str] = ()) -> pd.DataFrame:
    """Rows valid for KPIs, typed, with net_revenue / gross_profit precomputed."""
    prepared = prepare_sales_frame(working, unavailable)
    valid = prepared[prepared["kpi_valid"] == 1].copy()
    valid["year"] = valid["order_date"].dt.year.astype("Int64")
    valid["month"] = valid["order_date"].dt.month.astype("Int64")
    valid["order_date"] = valid["order_date"].dt.strftime("%Y-%m-%d")
    valid["net_revenue"] = valid["net_revenue"].round(2)
    valid["gross_profit"] = valid["gross_profit"].round(2)
    return valid[POWERBI_COLUMNS].reset_index(drop=True)


def generate_powerbi_package(working: pd.DataFrame, folder: str | Path,
                             source_name: str = "", data_state: str = "",
                             unavailable: Collection[str] = (), import_settings: str = "") -> list[Path]:
    """Write sales_clean.csv, the guide, the data dictionary and package_info.txt.

    unavailable: columns marked "not available" in the column mapping.
    import_settings: how the file was read and its numbers/dates parsed (formats.import_summary).
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ExportError(f"Folder does not exist: {folder}")

    frame = powerbi_frame(working, unavailable)
    summary = validity_summary(prepare_sales_frame(working, unavailable))
    written = [export_csv(frame, folder / POWERBI_CSV_NAME)]

    for doc in POWERBI_DOCS:
        src = resource_path("powerbi", doc)
        target = folder / doc
        try:
            shutil.copyfile(src, target)
        except OSError as exc:
            raise _friendly(exc, target) from exc
        written.append(target)

    info = folder / "package_info.txt"
    lines = [
        f"DataBizex v{__version__} - Power BI package",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        f"Source file: {source_name or '(unknown)'}",
        f"Data state: {data_state or '(not specified)'}",
        *([f"Import settings: {import_settings}"] if import_settings else []),
        f"Rows in working dataset: {summary.total_rows}",
        f"Rows exported to {POWERBI_CSV_NAME} (valid for KPIs): {summary.kpi_rows}",
        f"Rows excluded (invalid/missing quantity, price, discount or cost): {summary.excluded_rows}",
        f"Exported rows without a valid order_date (blank date): {summary.kpi_rows_without_valid_date}",
        "",
        "No .pbix file is included. Follow POWER_BI_GUIDE.md to build the report.",
    ]
    try:
        info.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise _friendly(exc, info) from exc
    written.append(info)
    logger.info("Power BI package written to %s", folder)
    return written
