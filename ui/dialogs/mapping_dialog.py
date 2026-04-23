from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from data.models import MappingRecord, SkillProcess


class MappingReviewDialog(QDialog):
    def __init__(
        self,
        records: list[MappingRecord],
        skill_processes: list[SkillProcess],
        threshold: float,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.records = records
        self.threshold = threshold
        self._skill_names = [process.display_name for process in skill_processes if process.is_active]
        self._combos: list[QComboBox] = []

        self.setWindowTitle("人工确认映射")
        self.resize(1080, 560)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "AI 候选只做参考。请为每条工序选择已有标准工序，或直接输入新工序名称；"
                "系统会在保存时自动生成最终工序 ID。"
            )
        )

        self.table = QTableWidget(len(records), 6)
        self.table.setHorizontalHeaderLabels(["工序描述", "AI候选", "最终标准工序", "置信度", "需确认", "原因"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)

        for row, record in enumerate(records):
            needs_review = record.confidence < threshold or record.suggested_new_skill or not record.human_approved
            base_values = [
                record.process_description,
                record.llm_skill_name,
                f"{record.confidence:.2f}",
                "是" if needs_review else "否",
                record.reason,
            ]
            for col, value in ((0, base_values[0]), (1, base_values[1]), (3, base_values[2]), (4, base_values[3]), (5, base_values[4])):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if needs_review:
                    item.setBackground(Qt.GlobalColor.yellow)
                self.table.setItem(row, col, item)

            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItems(self._skill_names)
            initial_name = record.final_skill_name.strip() or record.llm_skill_name.strip()
            if initial_name and combo.findText(initial_name) < 0:
                combo.addItem(initial_name)
            combo.setCurrentText(initial_name)
            if needs_review:
                combo.setStyleSheet("QComboBox { background: #fff6dd; }")
            self.table.setCellWidget(row, 2, combo)
            self._combos.append(combo)

        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def confirmed_records(self) -> list[MappingRecord]:
        for row, record in enumerate(self.records):
            final_skill = self._combos[row].currentText().strip()
            record.final_skill_name = final_skill
            record.human_approved = bool(final_skill)
            record.suggested_new_skill = False
        return self.records
