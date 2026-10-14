"""Modal "Fix" dialog for one failed rule; the fix logic lives in app.data.fixes.

The user picks one of the suggested actions, sets its parameters and sees a live
preview (before -> after, up to 20 rows) and the counts and quality score change,
all computed on a copy. Nothing changes until Apply; the caller applies fix_result().
"""

from __future__ import annotations

from collections.abc import Collection

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.data import fixes
from app.data.change_log import ChangeEntry
from app.data.fixes import FixAction, FixError
from app.data.rules import Rule
from app.ui import theme
from app.ui.data_table import DataTable

PREVIEW_DELAY_MS = 250  # wait for typing to pause before recomputing the preview


def _score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


class FixDialog(QDialog):
    def __init__(self, df: pd.DataFrame, rule: Rule, rules: list[Rule], failed_rows: int,
                 unavailable: Collection[str] = (), parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Fix: {rule.name}")
        self.setModal(True)
        self.resize(760, 640)
        self._df, self._rule, self._rules, self._unavailable = df, rule, rules, list(unavailable)
        self._before = fixes.measure(df, rules, unavailable)  # computed once; the preview reuses it
        self._result: tuple[pd.DataFrame, ChangeEntry] | None = None
        column = df[rule.column] if rule.column in df.columns else None
        self.actions = fixes.suggested_actions(rule, column.dtype if column is not None else None)

        layout = QVBoxLayout(self)
        title = QLabel(rule.name)
        theme.set_role(title, "dialogTitle")
        layout.addWidget(title)
        problem = QLabel(f"{fixes.explain(rule)} <b>{failed_rows:,} row{'s' if failed_rows != 1 else ''}</b> "
                         "fail this check in the working data.")
        problem.setWordWrap(True)
        layout.addWidget(problem)
        safety = QLabel("Only the working copy changes; your file and the original import stay as they are. "
                        "Every fix is logged and can be undone.")
        safety.setWordWrap(True)
        theme.set_role(safety, "muted")
        layout.addWidget(safety)

        choice = QGroupBox("What to do")
        choice_layout = QVBoxLayout(choice)
        self.action_group = QButtonGroup(self)
        self.radios: dict[FixAction, QRadioButton] = {}
        for i, action in enumerate(self.actions):
            radio = QRadioButton(fixes.DESCRIPTIONS[action])
            self.radios[action] = radio
            self.action_group.addButton(radio, i)
            choice_layout.addWidget(radio)
        layout.addWidget(choice)

        self.params = QStackedWidget()
        self.param_pages: dict[FixAction, int] = {}
        for action in self.actions:
            self.param_pages[action] = self.params.addWidget(self._param_page(action))
        layout.addWidget(self.params)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        theme.set_role(self.summary, "strong")
        layout.addWidget(self.summary)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        preview_box = QGroupBox("Preview (before -> after, up to 20 rows)")
        preview_layout = QVBoxLayout(preview_box)
        self.preview = DataTable()
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_box, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_btn = self.buttons.addButton("Apply", QDialogButtonBox.AcceptRole)
        theme.mark_primary(self.apply_btn)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._timer = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self.update_preview)
        self.action_group.idClicked.connect(self._action_changed)
        if self.actions:
            self.radios[self.actions[0]].setChecked(True)
            self._action_changed(0)
        else:
            self.apply_btn.setEnabled(False)
            self.summary.setText("No automatic fix is offered for this check.")

    # ------------------------------------------------------------------ parameters
    def _param_page(self, action: FixAction) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        if action is FixAction.MAP_VALUES:
            hint = QLabel("Type the replacement for each value you want to change. Rows left empty are "
                          "not changed. Matching ignores case and extra spaces.")
            hint.setWordWrap(True)
            theme.set_role(hint, "muted")
            layout.addWidget(hint)
            candidates = fixes.mapping_candidates(self._df, self._rule)
            self.mapping_table = QTableWidget(len(candidates), 3)
            self.mapping_table.setHorizontalHeaderLabels(["Current value", "Count", "Replace with"])
            self.mapping_table.verticalHeader().setVisible(False)
            self.mapping_table.setEditTriggers(QAbstractItemView.AllEditTriggers)
            self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            for row, (value, count, suggestion) in enumerate(candidates):
                for col, text in enumerate((value, f"{count:,}", suggestion)):
                    item = QTableWidgetItem(text)
                    if col < 2:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if col == 1:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.mapping_table.setItem(row, col, item)
            self.mapping_table.setMinimumHeight(140)
            self.mapping_table.itemChanged.connect(lambda _item: self._timer.start())
            layout.addWidget(self.mapping_table)
        elif action is FixAction.FILL_CONSTANT:
            column = self._df[self._rule.column]
            kind = fixes.column_kind(column, self._rule)
            form = QFormLayout()
            self.constant_edit = QLineEdit()
            self.constant_edit.setPlaceholderText({"numeric": "a number, e.g. 0", "date": "a date, YYYY-MM-DD"}
                                                  .get(kind, "text"))
            self.constant_edit.textChanged.connect(lambda _text: self._timer.start())
            form.addRow(f"Value for {self._rule.column} ({kind}):", self.constant_edit)
            layout.addLayout(form)
        elif action is FixAction.TRIM_AND_CASE:
            row = QHBoxLayout()
            row.addWidget(QLabel("Letter case:"))
            self.case_combo = QComboBox()
            for key, label in fixes.CASE_OPTIONS.items():
                self.case_combo.addItem(label, key)
            self.case_combo.currentIndexChanged.connect(lambda _i: self._timer.start())
            row.addWidget(self.case_combo)
            row.addStretch(1)
            layout.addLayout(row)
        return page

    def current_action(self) -> FixAction | None:
        index = self.action_group.checkedId()
        return self.actions[index] if 0 <= index < len(self.actions) else None

    def parameters(self) -> dict:
        action = self.current_action()
        if action is FixAction.MAP_VALUES:
            mapping = {}
            for row in range(self.mapping_table.rowCount()):
                target = self.mapping_table.item(row, 2)
                if target is not None and target.text().strip():
                    mapping[self.mapping_table.item(row, 0).text()] = target.text()
            return {"mapping": mapping}
        if action is FixAction.FILL_CONSTANT:
            return {"value": self.constant_edit.text()}
        if action is FixAction.TRIM_AND_CASE:
            return {"case": self.case_combo.currentData()}
        return {}

    # ------------------------------------------------------------------ preview
    def _action_changed(self, _index: int) -> None:
        action = self.current_action()
        if action is not None:
            self.params.setCurrentIndex(self.param_pages[action])
        self.update_preview()

    def update_preview(self) -> None:
        """Run the chosen fix on a copy and show its effect; nothing is applied."""
        self._timer.stop()
        action = self.current_action()
        self._result = None
        if action is None:
            return
        try:
            out, entry = fixes.run_fix(self._df, self._rule, action, self.parameters(), self._rules,
                                       self._unavailable, before=self._before)
        except FixError as exc:
            self.summary.setText("Nothing will change yet.")
            self.detail.setText(str(exc))
            theme.set_role(self.detail, "error")
            self.preview.show_frame(None)
            self.apply_btn.setEnabled(False)
            return
        self._result = (out, entry)
        rows = f"{entry.rows_affected:,} row{'s' if entry.rows_affected != 1 else ''}"
        cells = f"{entry.cells_changed:,} cell{'s' if entry.cells_changed != 1 else ''}"
        self.summary.setText(f"{rows} / {cells} will change. Quality score: "
                             f"{_score(entry.quality_score_before)} -> {_score(entry.quality_score_after)}")
        valid = ""
        if entry.valid_rows_before is not None:
            valid = f" Rows valid for KPIs: {entry.valid_rows_before:,} -> {entry.valid_rows_after:,}."
        self.detail.setText(fixes.what_it_will_do(entry) + valid)
        theme.set_role(self.detail, "muted")
        samples = pd.DataFrame(entry.samples, columns=["source_row", "column", "before", "after"])
        self.preview.show_frame(samples)
        self.apply_btn.setEnabled(entry.rows_affected > 0)

    def fix_result(self) -> tuple[pd.DataFrame, ChangeEntry] | None:
        """(new working copy, change entry) of the previewed fix, once Apply was clicked."""
        return self._result

    def accept(self) -> None:
        if self._timer.isActive():  # typing just stopped: make sure the preview is current
            self.update_preview()
        if self._result is None or self._result[1].rows_affected == 0:
            return
        super().accept()
