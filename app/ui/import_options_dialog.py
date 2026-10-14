"""Modal "Import Options" dialog; detection and conversion live in app.data.formats."""

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

from app.data.formats import (
    DATE_FORMATS,
    DECIMALS,
    DELIMITERS,
    ENCODINGS,
    THOUSANDS,
    FormatDetection,
    ImportOptions,
    options_problem,
)
from app.data.layout_memory import REMEMBERED_NOTE

DETECTED_SUFFIX = "  (detected)"


class ImportOptionsDialog(QDialog):
    def __init__(self, file_name: str, detection: FormatDetection, parent=None,
                 remembered: ImportOptions | None = None) -> None:
        """remembered: settings from a previous session, preselected instead of the detected ones."""
        super().__init__(parent)
        self.setWindowTitle("Import Options")
        self.setModal(True)
        detected = detection.options
        initial = remembered or detected
        self._is_excel = detected.delimiter is None

        layout = QVBoxLayout(self)
        lines = [f"<b>{REMEMBERED_NOTE}</b>"] if remembered else []
        lines += [f"DataBizex detected these settings in <b>{file_name}</b>:",
                 f"&nbsp;&nbsp;{detected.describe()}"]
        if detection.decimal_columns:
            lines.append("&nbsp;&nbsp;Numbers with these separators in: " + ", ".join(detection.decimal_columns))
        date_columns = [c for c, f in detection.date_columns.items() if f == detected.date_format]
        if date_columns:
            lines.append("&nbsp;&nbsp;Dates in: " + ", ".join(date_columns))
        lines += [f"&nbsp;&nbsp;<span style='color:#8a4b00'>{note}</span>" for note in detection.notes]
        lines.append("Nothing is applied until you confirm. Change any setting that is wrong. "
                     "Numbers and dates are converted in the working copy only; your file and the "
                     "original import are not changed.<br>Cancel loads the file as read, without "
                     "number or date conversion.")
        intro = QLabel("<br>".join(lines))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.delimiter = self._combo(DELIMITERS, detected.delimiter, initial.delimiter)
        self.encoding = self._combo(ENCODINGS, detected.encoding, initial.encoding)
        self.decimal = self._combo(DECIMALS, detected.decimal, initial.decimal)
        self.thousands = self._combo(THOUSANDS, detected.thousands, initial.thousands)
        self.date_format = self._combo(DATE_FORMATS, detected.date_format, initial.date_format)
        for combo in (self.delimiter, self.encoding):
            combo.setEnabled(not self._is_excel)
            if self._is_excel:
                combo.setToolTip("Not used for Excel files")
        form.addRow("Delimiter", self.delimiter)
        form.addRow("Encoding", self.encoding)
        form.addRow("Decimal separator", self.decimal)
        form.addRow("Thousands separator", self.thousands)
        form.addRow("Date format", self.date_format)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _combo(choices: dict[str, str], detected: str | None, selected: str | None) -> QComboBox:
        combo = QComboBox()
        for value, label in choices.items():
            combo.addItem(label[:1].upper() + label[1:] + (DETECTED_SUFFIX if value == detected else ""), value)
        index = combo.findData(selected)
        if index < 0:
            index = combo.findData(detected)
        combo.setCurrentIndex(max(index, 0))
        return combo

    def options(self) -> ImportOptions:
        return ImportOptions(
            delimiter=None if self._is_excel else self.delimiter.currentData(),
            encoding=None if self._is_excel else self.encoding.currentData(),
            decimal=self.decimal.currentData(),
            thousands=self.thousands.currentData(),
            date_format=self.date_format.currentData(),
        )

    def accept(self) -> None:
        problem = options_problem(self.options())
        if problem:
            QMessageBox.warning(self, "Check the import options", problem)
            return
        super().accept()
