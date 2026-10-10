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

STATUS_COLOURS = {
    "OK": QColor("#d8f0dc"),
    "Issue": QColor("#fde2c8"),
    "Unavailable": QColor("#e4e4e4"),
}


class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None, float_decimals: int | None = None) -> None:
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()
        self._float_decimals = float_decimals

    def set_frame(self, df: pd.DataFrame | None, float_decimals: int | None = None) -> None:
        self.beginResetModel()
        self._df = df if df is not None else pd.DataFrame()
        self._float_decimals = float_decimals
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._df.shape[1]

    def _format(self, value: object) -> str:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ""
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
            return self._format(value)
        if role == Qt.TextAlignmentRole and isinstance(value, numbers.Number) and not isinstance(value, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.BackgroundRole and self._df.columns[index.column()] == "status":
            colour = STATUS_COLOURS.get(str(value))
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
        self.verticalHeader().setDefaultSectionSize(22)

    def show_frame(self, df: pd.DataFrame | None, float_decimals: int | None = None) -> None:
        self.model_.set_frame(df, float_decimals)
        self.resizeColumnsToContents()
        for col in range(self.model_.columnCount()):
            if self.columnWidth(col) > 320:
                self.setColumnWidth(col, 320)
