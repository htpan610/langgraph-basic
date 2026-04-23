from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView

from core.distance_calculator import total_manhattan_distance
from data.models import Flow, Station


class StationItem(QGraphicsRectItem):
    def __init__(self, station: Station, width: int, height: int, on_moved: Callable[[], None]) -> None:
        super().__init__(0, 0, width, height)
        self.station = station
        self.on_moved = on_moved
        self.setPos(station.x, station.y)
        self.setBrush(QBrush(QColor("#f4f8fb")))
        self.setPen(QPen(QColor("#2f6f8f"), 2))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        text = f"{station.station_id}  {station.employee_name}\n负荷 {station.load_time:.2f}\n{len(station.assigned_processes)} 道工序"
        label = QGraphicsSimpleTextItem(text, self)
        label.setFont(QFont("Microsoft YaHei", 9))
        label.setPos(8, 8)

    def mouseReleaseEvent(self, event) -> None:
        pos = self.pos()
        # Persist drag results back onto the domain object so distance/export use the latest coordinates.
        self.station.x = float(pos.x())
        self.station.y = float(pos.y())
        self.on_moved()
        super().mouseReleaseEvent(event)


class LayoutGraphicsView(QGraphicsView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.stations: list[Station] = []
        self.flows: list[Flow] = []
        self.on_distance_changed: Callable[[float], None] | None = None

    def render_layout(self, stations: list[Station], flows: list[Flow], width: int, height: int) -> None:
        self.scene.clear()
        self.stations = stations
        self.flows = flows
        # Station items are recreated from current result state each render; there is no separate view model.
        for station in stations:
            self.scene.addItem(StationItem(station, width, height, self.recalculate_distance))
        self.scene.setSceneRect(QRectF(-80, -80, 1800, 720))
        self.recalculate_distance()

    def recalculate_distance(self) -> None:
        distance = total_manhattan_distance(self.stations, self.flows)
        if self.on_distance_changed:
            self.on_distance_changed(distance)
