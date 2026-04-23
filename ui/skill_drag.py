from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QMimeData, Qt
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QListWidget, QTableWidget


SKILL_PROCESS_MIME = "application/x-langgraph-skill-process"


class UncoveredSkillTable(QTableWidget):
    def __init__(self, rows: int = 0, columns: int = 0, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QTableWidget.DragOnly)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        row = self.currentRow()
        if row < 0:
            return
        process_id_item = self.item(row, 0)
        process_name_item = self.item(row, 1)
        source_item = self.item(row, 2)
        if process_id_item is None or process_name_item is None:
            return
        payload = {
            "process_id": process_id_item.text().strip(),
            "process_name": process_name_item.text().strip(),
            "source": source_item.text().strip() if source_item else "",
        }
        if not payload["process_id"]:
            return
        mime = QMimeData()
        mime.setData(SKILL_PROCESS_MIME, QByteArray(json.dumps(payload).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class EmployeeDropListWidget(QListWidget):
    def __init__(self, on_drop, parent=None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(SKILL_PROCESS_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(SKILL_PROCESS_MIME):
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self.setCurrentItem(item)
                event.acceptProposedAction()
                return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasFormat(SKILL_PROCESS_MIME):
            super().dropEvent(event)
            return
        item = self.itemAt(event.position().toPoint())
        if item is None:
            event.ignore()
            return
        payload = json.loads(bytes(event.mimeData().data(SKILL_PROCESS_MIME)).decode("utf-8"))
        self.setCurrentItem(item)
        self._on_drop(item, payload)
        event.acceptProposedAction()
