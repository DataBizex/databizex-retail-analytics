"""Modal "Map Your Columns" dialog; the mapping rules live in app.data.column_mapping."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from app.data.column_mapping import EXPECTED_COLUMNS, NOT_AVAILABLE_LABEL, mapping_problems


class ColumnMappingDialog(QDialog):
    def __init__(self, columns: list[str], guess: dict[str, str | None], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map Your Columns")
        self.setModal(True)
        self._columns = [str(c) for c in columns]

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Some columns DataBizex uses for KPIs were not found by name. Choose which column of "
            "your file holds each field, or pick “(not available)”. Your file is not changed.\n\n"
            "Cancel keeps the file loaded without mapping (KPIs may be unavailable)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for target in EXPECTED_COLUMNS:
            combo = QComboBox()
            combo.addItem(NOT_AVAILABLE_LABEL, None)
            for col in self._columns:
                combo.addItem(col, col)
            index = combo.findData(guess.get(target))
            combo.setCurrentIndex(max(index, 0))
            self.combos[target] = combo
            form.addRow(QLabel(target), combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mapping(self) -> dict[str, str | None]:
        return {target: combo.currentData() for target, combo in self.combos.items()}

    def accept(self) -> None:
        problems = mapping_problems(self._columns, self.mapping())
        if problems:
            QMessageBox.warning(self, "Check the mapping", "\n\n".join(problems))
            return
        super().accept()
