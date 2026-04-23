from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data.models import AssignmentResult, Metrics, Process, Station


_COMPONENT_STYLES = [
    {"chip_bg": "#dbeafe", "chip_fg": "#1d4ed8", "border": "#bfdbfe", "soft": "#eff6ff"},
    {"chip_bg": "#dcfce7", "chip_fg": "#166534", "border": "#bbf7d0", "soft": "#f0fdf4"},
    {"chip_bg": "#fef3c7", "chip_fg": "#b45309", "border": "#fde68a", "soft": "#fffbeb"},
    {"chip_bg": "#fae8ff", "chip_fg": "#9333ea", "border": "#e9d5ff", "soft": "#faf5ff"},
    {"chip_bg": "#ffe4e6", "chip_fg": "#be123c", "border": "#fecdd3", "soft": "#fff1f2"},
    {"chip_bg": "#cffafe", "chip_fg": "#155e75", "border": "#a5f3fc", "soft": "#ecfeff"},
]


def _process_sort_key(text: str) -> tuple[int, int, str]:
    stripped = str(text or "").strip()
    digits = "".join(ch for ch in stripped if ch.isdigit())
    if digits:
        return (0, int(digits), stripped)
    return (1, 0, stripped)


def _component_style(name: str) -> dict[str, str]:
    key = sum(ord(ch) for ch in name) if name else 0
    return _COMPONENT_STYLES[key % len(_COMPONENT_STYLES)]


def _load_level(load_ratio: float) -> tuple[str, str]:
    if load_ratio >= 0.92:
        return ("高负载", "#166534")
    if load_ratio >= 0.75:
        return ("均衡", "#1d4ed8")
    return ("可加负荷", "#b45309")


class ClickableFrame(QFrame):
    def __init__(self, on_click=None, parent=None) -> None:
        super().__init__(parent)
        self._on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if self._on_click is not None and event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class BalancingMetricsWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: dict[str, QLabel] = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        for key, title in [
            ("balance_rate", "平衡率"),
            ("cycle_time", "节拍"),
            ("total_effective_time", "总有效工时"),
            ("total_distance", "总搬运距离"),
            ("num_stations", "工位数"),
        ]:
            card = QFrame()
            card.setStyleSheet(
                "QFrame {"
                "background: #f8fbff;"
                "border: 1px solid #d7e5f4;"
                "border-radius: 14px;"
                "}"
            )
            layout = QVBoxLayout(card)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(4)
            title_label = QLabel(title)
            title_label.setStyleSheet("color: #60758a; font-size: 12px;")
            value_label = QLabel("-")
            value_label.setStyleSheet("color: #17324d; font-size: 20px; font-weight: 700;")
            layout.addWidget(title_label)
            layout.addWidget(value_label)
            self._cards[key] = value_label
            root.addWidget(card, 1)

    def set_metrics(self, metrics: Metrics | None) -> None:
        if metrics is None:
            for label in self._cards.values():
                label.setText("-")
            return
        self._cards["balance_rate"].setText(f"{metrics.balance_rate * 100:.1f}%")
        self._cards["cycle_time"].setText(f"{metrics.cycle_time:.2f}")
        self._cards["total_effective_time"].setText(f"{metrics.total_effective_time:.2f}")
        self._cards["total_distance"].setText(f"{metrics.total_distance:.1f}")
        self._cards["num_stations"].setText(str(metrics.num_stations))


class EmployeeAssignmentsDialog(QDialog):
    def __init__(self, station: Station, cycle_time: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"员工工序明细 - {station.employee_name}")
        self.resize(980, 620)

        load_ratio = station.load_time / cycle_time if cycle_time > 0 else 0.0
        load_text, load_color = _load_level(load_ratio)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        title = QLabel(f"{station.employee_name}  {station.station_id}")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #18314b;")
        root.addWidget(title)

        summary = QLabel(
            f"负载 {station.load_time:.2f} / 节拍 {cycle_time:.2f}    "
            f"负荷率 {load_ratio * 100:.1f}%    "
            f"工序数 {len(station.assigned_processes)}    "
            f"{load_text}"
        )
        summary.setStyleSheet(f"color: {load_color}; font-size: 13px; font-weight: 600;")
        root.addWidget(summary)

        table = QTableWidget(0, 6)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setHorizontalHeaderLabels(["工序号", "模块", "标准工序", "原工序描述", "标准工时", "分配负载"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)

        rows = sorted(
            station.assigned_processes,
            key=lambda item: _process_sort_key(str(item.get("process_no", ""))),
        )
        table.setRowCount(len(rows))
        for row, process in enumerate(rows):
            values = [
                str(process.get("process_no", "")),
                str(process.get("component", "")),
                str(process.get("standard_process_name", "")),
                str(process.get("description", "")),
                f"{float(process.get('standard_time', 0.0)):.2f}",
                f"{float(process.get('effective_time', 0.0)):.2f}",
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        root.addWidget(table, 1)


class BalancingOverviewWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: AssignmentResult | None = None
        self._processes: list[Process] = []
        self._station_by_employee: dict[str, Station] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.empty_state = QLabel("运行排产后，这里会展示模块工序分配、工位负载和员工任务总览。")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setStyleSheet(
            "color: #61778b;"
            "padding: 42px 24px;"
            "border: 1px dashed #cfdceb;"
            "border-radius: 16px;"
            "background: #fbfdff;"
        )
        root.addWidget(self.empty_state)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        splitter = QHBoxLayout()
        splitter.setSpacing(12)

        board_group = QGroupBox("模块工序分配")
        board_group.setStyleSheet("QGroupBox { font-weight: 700; }")
        board_layout = QVBoxLayout(board_group)
        board_layout.setContentsMargins(12, 18, 12, 12)
        board_layout.setSpacing(10)
        board_hint = QLabel("主窗口按模块展示所有工序，并直接标明分配员工和工位。")
        board_hint.setStyleSheet("color: #70859a; font-size: 12px;")
        board_layout.addWidget(board_hint)
        self.board_scroll = QScrollArea()
        self.board_scroll.setWidgetResizable(True)
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.board_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.board_content = QWidget()
        self.board_columns = QHBoxLayout(self.board_content)
        self.board_columns.setContentsMargins(0, 0, 0, 0)
        self.board_columns.setSpacing(14)
        self.board_scroll.setWidget(self.board_content)
        board_layout.addWidget(self.board_scroll, 1)
        splitter.addWidget(board_group, 3)

        station_group = QGroupBox("工位负载")
        station_group.setStyleSheet("QGroupBox { font-weight: 700; }")
        station_layout = QVBoxLayout(station_group)
        station_layout.setContentsMargins(12, 18, 12, 12)
        station_layout.setSpacing(10)
        station_hint = QLabel("每个工位都会显示负载、负荷率和工序数量。")
        station_hint.setStyleSheet("color: #70859a; font-size: 12px;")
        station_layout.addWidget(station_hint)
        self.station_table = QTableWidget(0, 6)
        self.station_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.station_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.station_table.setAlternatingRowColors(True)
        self.station_table.setHorizontalHeaderLabels(["工位", "员工", "工序数", "负载", "负荷率", "明细"])
        self.station_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.station_table.verticalHeader().setVisible(False)
        station_layout.addWidget(self.station_table, 1)
        splitter.addWidget(station_group, 2)

        content_layout.addLayout(splitter, 1)

        employee_group = QGroupBox("员工任务总览")
        employee_group.setStyleSheet("QGroupBox { font-weight: 700; }")
        employee_layout = QVBoxLayout(employee_group)
        employee_layout.setContentsMargins(12, 18, 12, 12)
        employee_layout.setSpacing(10)
        employee_hint = QLabel("点击员工卡片可打开次窗口，查看该员工被分配到的全部工序。")
        employee_hint.setStyleSheet("color: #70859a; font-size: 12px;")
        employee_layout.addWidget(employee_hint)
        self.employee_scroll = QScrollArea()
        self.employee_scroll.setWidgetResizable(True)
        self.employee_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.employee_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.employee_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.employee_content = QWidget()
        self.employee_cards = QHBoxLayout(self.employee_content)
        self.employee_cards.setContentsMargins(0, 0, 0, 0)
        self.employee_cards.setSpacing(12)
        self.employee_scroll.setWidget(self.employee_content)
        employee_layout.addWidget(self.employee_scroll, 1)
        content_layout.addWidget(employee_group)

        root.addWidget(self.content, 1)
        self.content.hide()

    def set_result(self, result: AssignmentResult | None, processes: list[Process]) -> None:
        self._result = result
        self._processes = list(processes)
        self._station_by_employee = {}
        if result is None:
            self._set_empty_state(True)
            self._clear_layout(self.board_columns)
            self._clear_layout(self.employee_cards)
            self.station_table.setRowCount(0)
            return
        self._station_by_employee = {station.employee_id: station for station in result.stations}
        self._set_empty_state(False)
        self._render_board()
        self._render_station_table()
        self._render_employee_cards()

    def _set_empty_state(self, empty: bool) -> None:
        self.empty_state.setVisible(empty)
        self.content.setVisible(not empty)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)

    def _render_board(self) -> None:
        self._clear_layout(self.board_columns)
        if not self._result:
            return

        assigned_by_process: dict[str, dict[str, Any]] = {}
        for station in self._result.stations:
            for process in station.assigned_processes:
                assigned_by_process[str(process.get("process_id", ""))] = {
                    **process,
                    "_employee_name": station.employee_name,
                    "_employee_id": station.employee_id,
                    "_station_id": station.station_id,
                }

        component_rows: dict[str, list[tuple[Process, dict[str, Any] | None]]] = {}
        ordered_processes = sorted(self._processes, key=lambda item: (item.sort_order, _process_sort_key(item.process_no)))
        for process in ordered_processes:
            component = process.component or "未分类"
            component_rows.setdefault(component, []).append((process, assigned_by_process.get(process.id)))

        for component, rows in component_rows.items():
            style = _component_style(component)
            column = QWidget()
            column.setMinimumWidth(290)
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(10)

            total_standard = sum(process.standard_time for process, _assigned in rows)
            employees = {assigned["_employee_name"] for _process, assigned in rows if assigned}
            header = QFrame()
            header.setStyleSheet(
                f"QFrame {{ background: {style['soft']}; border: 1px solid {style['border']}; border-radius: 16px; }}"
            )
            header_layout = QVBoxLayout(header)
            header_layout.setContentsMargins(14, 12, 14, 12)
            header_layout.setSpacing(4)
            title = QLabel(component)
            title.setStyleSheet(f"color: {style['chip_fg']}; font-size: 16px; font-weight: 700;")
            summary = QLabel(f"{len(rows)} 道工序   {len(employees)} 名员工   标准工时 {total_standard:.2f}")
            summary.setStyleSheet("color: #5f7387; font-size: 12px;")
            header_layout.addWidget(title)
            header_layout.addWidget(summary)
            column_layout.addWidget(header)

            for index, (process, assigned) in enumerate(rows):
                column_layout.addWidget(self._build_process_card(process, assigned))
                if index < len(rows) - 1:
                    arrow = QLabel("↓")
                    arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    arrow.setStyleSheet("color: #9db0c2; font-size: 18px; font-weight: 700;")
                    column_layout.addWidget(arrow)
            column_layout.addStretch(1)
            self.board_columns.addWidget(column)
        self.board_columns.addStretch(1)

    def _build_process_card(self, process: Process, assigned: dict[str, Any] | None) -> QWidget:
        component = process.component or "未分类"
        style = _component_style(component)
        card = QFrame()
        card.setStyleSheet(
            "QFrame {"
            "background: white;"
            "border: 1px dashed #d7e3ef;"
            "border-radius: 16px;"
            "}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        chip = QLabel(component)
        chip.setStyleSheet(
            f"background: {style['chip_bg']};"
            f"color: {style['chip_fg']};"
            "padding: 3px 10px;"
            "border-radius: 10px;"
            "font-size: 12px;"
            "font-weight: 700;"
        )
        process_no = QLabel(f"# {process.process_no or '-'}")
        process_no.setStyleSheet("color: #7a8ea3; font-size: 12px;")
        top_row.addWidget(chip)
        top_row.addStretch(1)
        top_row.addWidget(process_no)
        layout.addLayout(top_row)

        skill_name = str(assigned.get("standard_process_name", "")) if assigned else ""
        primary = QLabel(skill_name or process.description)
        primary.setWordWrap(True)
        primary.setStyleSheet("color: #17324d; font-size: 16px; font-weight: 700;")
        layout.addWidget(primary)

        if skill_name and skill_name != process.description:
            original = QLabel(process.description)
            original.setWordWrap(True)
            original.setStyleSheet("color: #70859a; font-size: 12px;")
            layout.addWidget(original)

        assigned_name = assigned.get("_employee_name", "未分配") if assigned else "未分配"
        station_id = assigned.get("_station_id", "-") if assigned else "-"
        assignee = QLabel(f"分配给：{assigned_name} · {station_id}")
        assignee.setStyleSheet("color: #20506b; font-size: 13px; font-weight: 600;")
        layout.addWidget(assignee)

        timing = QLabel(
            f"标准工时 {process.standard_time:.2f}    分配负载 {float((assigned or {}).get('effective_time', 0.0)):.2f}"
        )
        timing.setStyleSheet("color: #6f8296; font-size: 12px;")
        layout.addWidget(timing)
        return card

    def _render_station_table(self) -> None:
        if not self._result:
            self.station_table.setRowCount(0)
            return
        cycle_time = self._result.metrics.cycle_time
        ordered_stations = sorted(self._result.stations, key=lambda item: _process_sort_key(item.station_id))
        self.station_table.setRowCount(len(ordered_stations))
        for row, station in enumerate(ordered_stations):
            load_ratio = station.load_time / cycle_time if cycle_time > 0 else 0.0
            load_text, _load_color_name = _load_level(load_ratio)

            self.station_table.setItem(row, 0, QTableWidgetItem(station.station_id))
            self.station_table.setItem(row, 1, QTableWidgetItem(station.employee_name))
            self.station_table.setItem(row, 2, QTableWidgetItem(str(len(station.assigned_processes))))
            self.station_table.setItem(row, 3, QTableWidgetItem(f"{station.load_time:.2f}"))

            progress = QProgressBar()
            progress.setRange(0, 1000)
            progress.setValue(min(1000, max(0, int(round(load_ratio * 1000)))))
            progress.setFormat(f"{load_ratio * 100:.1f}%  {load_text}")
            progress.setTextVisible(True)
            progress.setStyleSheet(
                "QProgressBar {"
                "border: 1px solid #d7e3ef;"
                "border-radius: 8px;"
                "background: #f7fbff;"
                "text-align: center;"
                "font-size: 11px;"
                "}"
                "QProgressBar::chunk {"
                "border-radius: 7px;"
                "background: #5b8def;"
                "}"
            )
            self.station_table.setCellWidget(row, 4, progress)

            button = QPushButton("查看")
            button.clicked.connect(partial(self._show_employee_dialog, station.employee_id))
            self.station_table.setCellWidget(row, 5, button)

    def _render_employee_cards(self) -> None:
        self._clear_layout(self.employee_cards)
        if not self._result:
            return

        cycle_time = self._result.metrics.cycle_time
        ordered_stations = sorted(self._result.stations, key=lambda item: _process_sort_key(item.station_id))
        for station in ordered_stations:
            load_ratio = station.load_time / cycle_time if cycle_time > 0 else 0.0
            load_text, load_color = _load_level(load_ratio)
            preview_names = [str(item.get("standard_process_name", item.get("description", ""))) for item in station.assigned_processes[:2]]
            preview = " / ".join(name for name in preview_names if name)
            if len(station.assigned_processes) > 2:
                preview = f"{preview} 等 {len(station.assigned_processes)} 道工序"

            card = ClickableFrame(partial(self._show_employee_dialog, station.employee_id))
            card.setMinimumWidth(220)
            card.setStyleSheet(
                "QFrame {"
                "background: white;"
                "border: 1px solid #d7e3ef;"
                "border-radius: 16px;"
                "}"
            )
            layout = QVBoxLayout(card)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(8)

            title_row = QHBoxLayout()
            avatar = QLabel(station.employee_name[:1] if station.employee_name else "?")
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setFixedSize(34, 34)
            avatar.setStyleSheet(
                "background: #1d4ed8;"
                "color: white;"
                "font-size: 16px;"
                "font-weight: 700;"
                "border-radius: 17px;"
            )
            title_box = QVBoxLayout()
            title_box.setContentsMargins(0, 0, 0, 0)
            title_box.setSpacing(2)
            name = QLabel(station.employee_name)
            name.setStyleSheet("color: #17324d; font-size: 15px; font-weight: 700;")
            station_label = QLabel(f"{station.station_id} · {len(station.assigned_processes)} 道工序")
            station_label.setStyleSheet("color: #70859a; font-size: 12px;")
            title_box.addWidget(name)
            title_box.addWidget(station_label)
            title_row.addWidget(avatar)
            title_row.addLayout(title_box, 1)
            layout.addLayout(title_row)

            progress = QProgressBar()
            progress.setRange(0, 1000)
            progress.setValue(min(1000, max(0, int(round(load_ratio * 1000)))))
            progress.setFormat(f"负荷率 {load_ratio * 100:.1f}%")
            progress.setStyleSheet(
                "QProgressBar {"
                "border: 1px solid #d7e3ef;"
                "border-radius: 8px;"
                "background: #f7fbff;"
                "text-align: center;"
                "height: 18px;"
                "}"
                "QProgressBar::chunk {"
                "border-radius: 7px;"
                "background: #60a5fa;"
                "}"
            )
            layout.addWidget(progress)

            load_label = QLabel(f"负载 {station.load_time:.2f} / 节拍 {cycle_time:.2f}    {load_text}")
            load_label.setStyleSheet(f"color: {load_color}; font-size: 12px; font-weight: 600;")
            layout.addWidget(load_label)

            preview_label = QLabel(preview or "点击查看该员工的详细工序清单")
            preview_label.setWordWrap(True)
            preview_label.setStyleSheet("color: #70859a; font-size: 12px;")
            layout.addWidget(preview_label)

            self.employee_cards.addWidget(card)
        self.employee_cards.addStretch(1)

    def _show_employee_dialog(self, employee_id: str) -> None:
        if not self._result:
            return
        station = self._station_by_employee.get(employee_id)
        if station is None:
            return
        dialog = EmployeeAssignmentsDialog(station, self._result.metrics.cycle_time, self)
        dialog.exec()
