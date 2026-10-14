"""Modal "Normalize Text" dialog; the grouping rules live in app.data.normalize."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.data.normalize import VariantGroup
from app.ui import theme


def _shown(value: str) -> str:
    """Quoted, with spaces made visible, so " germany" and "germany " can be told apart."""
    return "“" + value.replace(" ", "·") + "”"


class NormalizeDialog(QDialog):
    def __init__(self, variants: dict[str, list[VariantGroup]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Normalize Text")
        self.setModal(True)
        self.resize(640, 480)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "These columns contain the same value written with different case or spacing "
            "(· marks a space). Tick the columns to normalize; each group is written like its most "
            "frequent variant, with leading and trailing spaces removed and single spaces "
            "inside. Values spelled differently (e.g. “Deutschland” and "
            "“Germany”) are never merged.<br>Nothing changes until you click <b>Apply</b>. Only the "
            "working copy is changed; <b>Reset working copy to original</b> undoes it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Column / variant", "Rows", "Becomes"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.columns: dict[str, QTreeWidgetItem] = {}
        for column, groups in variants.items():
            changes = sum(g.changes for g in groups)
            top = QTreeWidgetItem([f"{column} — {len(groups)} group{'s' if len(groups) != 1 else ''}",
                                   f"{changes:,} to change", ""])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
            top.setCheckState(0, Qt.Checked)
            for group in groups:
                kind = "Whitespace only" if group.whitespace_only else f"{len(group.variants)} variants"
                node = QTreeWidgetItem([f"{_shown(group.canonical)} — {kind}",
                                        f"{group.changes:,} to change", _shown(group.canonical)])
                for value, count in group.variants:
                    target = "(kept)" if value == group.canonical else _shown(group.canonical)
                    node.addChild(QTreeWidgetItem([_shown(value), f"{count:,}", target]))
                top.addChild(node)
            self.tree.addTopLevelItem(top)
            self.tree.expandItem(top)
            for i in range(top.childCount()):
                self.tree.expandItem(top.child(i))
            self.columns[column] = top
        for col in (1, 2):
            self.tree.resizeColumnToContents(col)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        apply_btn = buttons.addButton("Apply", QDialogButtonBox.AcceptRole)
        apply_btn.setDefault(True)
        theme.mark_primary(apply_btn)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        return [column for column, item in self.columns.items() if item.checkState(0) == Qt.Checked]
