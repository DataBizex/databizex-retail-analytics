"""Read-only Qt table model/view for small pandas DataFrames.

The GUI never receives a full dataset: callers pass a preview (e.g. df.head(100))
or an aggregated result table.
"""

from __future__ import annotations

import numbers

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from app.data.display import format_whole
from app.ui import theme


def status_colour(status: str) -> QColor | None:
    """Background of a quality-summary status cell in the active theme."""
    p = theme.active()
    colour = {"OK": p.status_ok, "Issue": p.status_issue, "Unavailable": p.status_unavailable}.get(status)
    return QColor(colour) if colour else None


def failed_cell_colour() -> QColor:
    return QColor(theme.active().failed_cell)


class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None, float_decimals: int | None = None) -> None:
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()
        self._float_decimals = float_decimals
        self._highlight: set[tuple[int, int]] = set()
        self._whole: set[int] = set()

    def set_frame(self, df: pd.DataFrame | None, float_decimals: int | None = None,
                  highlight: set[tuple[int, str]] | None = None,
                  whole_number_columns: set[str] | None = None) -> None:
        """highlight: (row position, column name) cells to colour; whole_number_columns: float
        columns shown without a trailing ".0" (see app.data.display)."""
        self.beginResetModel()
        self._df = df if df is not None else pd.DataFrame()
        self._float_decimals = float_decimals
        positions = {name: i for i, name in enumerate(self._df.columns)}
        self._highlight = {(row, positions[col]) for row, col in highlight or () if col in positions}
        self._whole = {positions[col] for col in whole_number_columns or () if col in positions}
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._df.shape[1]

    def _format(self, value: object, whole: bool = False) -> str:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ""
        if whole and isinstance(value, float):
            return format_whole(value)
        if isinstance(value, float) and self._float_decimals is not None:
            return f"{value:,.{self._float_decimals}f}"
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y-%m-%d")
        return str(value)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self._df.iat[index.row(), index.column()]
        if role == Qt.DisplayRole:
            return self._format(value, index.column() in self._whole)
        if role == Qt.TextAlignmentRole and isinstance(value, numbers.Number) and not isinstance(value, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.BackgroundRole and (index.row(), index.column()) in self._highlight:
            return QBrush(failed_cell_colour())
        if role == Qt.BackgroundRole and self._df.columns[index.column()] == "status":
            colour = status_colour(str(value))
            return QBrush(colour) if colour else None
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return str(self._df.columns[section])
        return str(section + 1)


class DataTable(QTableView):
    """QTableView preconfigured for read-only DataFrame display."""

    def __init__(self, parent=None, stretch_last: bool = True) -> None:
        """stretch_last=False keeps every column at its content width (scrolling if needed),
        so a narrow panel never squeezes the last column's text."""
        super().__init__(parent)
        self.model_ = DataFrameModel()
        self.setModel(self.model_)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(stretch_last)
        self.verticalHeader().setDefaultSectionSize(26)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def show_frame(self, df: pd.DataFrame | None, float_decimals: int | None = None,
                   highlight: set[tuple[int, str]] | None = None,
                   whole_number_columns: set[str] | None = None) -> None:
        self.model_.set_frame(df, float_decimals, highlight, whole_number_columns)
        self.resizeColumnsToContents()
        for col in range(self.model_.columnCount()):
            if self.columnWidth(col) > 320:
                self.setColumnWidth(col, 320)
