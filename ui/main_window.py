from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.balancer import PulpBalancer
from core.config import Settings
from core.db import Repository
from core.distance_calculator import apply_straight_layout, default_flows
from core.exporter import ReportExporter
from core.ingestion import blocking_issues, load_processes, load_skill_matrix
from core.llm import DeepSeekProvider
from core.mapper import ProcessMapper, is_human_confirmed
from data.models import (
    AssignmentResult,
    DEFAULT_CATEGORY_ID,
    Employee,
    EmployeeSkill,
    MappingRecord,
    Process,
    SkillProcess,
    ValidationIssue,
)
from ui.balancing_overview import BalancingMetricsWidget, BalancingOverviewWidget
from ui.dialogs.mapping_dialog import MappingReviewDialog
from ui.graphics_view import LayoutGraphicsView
from ui.skill_drag import EmployeeDropListWidget, UncoveredSkillTable
from ui.workers import run_in_thread


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.repository = Repository(settings.app.database_path)
        self.llm = DeepSeekProvider(settings.llm)
        self.mapper = ProcessMapper(self.repository, self.llm, settings.llm)
        self.balancer = PulpBalancer(settings.balancing, repository=self.repository)
        self.exporter = ReportExporter()

        self.threads: list = []
        self.busy_started_at: float | None = None
        self.busy_message = ""
        self.busy_timer = QTimer(self)
        self.busy_timer.setInterval(1000)
        self.busy_timer.timeout.connect(self._refresh_busy_elapsed)

        self.employees: list[Employee] = []
        self.skill_processes: list[SkillProcess] = []
        self.employee_skills: list[EmployeeSkill] = []
        self.styles: list[str] = []
        self.style_processes: list[Process] = []
        self.style_issues: list[ValidationIssue] = []
        self.style_mappings: list[MappingRecord] = []
        self.skill_issues: list[ValidationIssue] = []
        self.uncovered_skill_processes: list[dict] = []
        self.result: AssignmentResult | None = None

        self.setWindowTitle(settings.app.name)
        self._build_ui()
        self._refresh_reference_data()
        self._update_buttons()

    def _build_ui(self) -> None:
        root_page = QWidget()
        root = QVBoxLayout(root_page)

        self.status_label = QLabel("请选择款式和人员后运行排产。")
        self.status_label.setStyleSheet("font-weight: 600; color: #20506b;")
        root.addWidget(self.status_label)

        tabs = QTabWidget()
        tabs.addTab(self._build_workflow_tab(), "排产")
        tabs.addTab(self._build_skill_matrix_tab(), "技能矩阵")
        tabs.addTab(self._build_worktime_library_tab(), "标准工时库")
        root.addWidget(tabs, 1)
        self.setCentralWidget(root_page)

    def _build_workflow_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        style_bar = QHBoxLayout()
        self.run_style_combo = QComboBox()
        self.run_style_combo.currentTextChanged.connect(self._on_run_style_changed)
        self.refresh_run_btn = QPushButton("刷新款式")
        self.refresh_run_btn.clicked.connect(self._refresh_reference_data)
        self.select_all_employees_btn = QPushButton("全选人员")
        self.select_all_employees_btn.clicked.connect(self._select_all_run_employees)
        self.clear_employees_btn = QPushButton("清空人员")
        self.clear_employees_btn.clicked.connect(lambda: self._select_run_employees(False))
        style_bar.addWidget(QLabel("款式"))
        style_bar.addWidget(self.run_style_combo, 1)
        style_bar.addWidget(self.refresh_run_btn)
        style_bar.addWidget(self.select_all_employees_btn)
        style_bar.addWidget(self.clear_employees_btn)
        root.addLayout(style_bar)

        action_bar = QHBoxLayout()
        self.balance_btn = QPushButton("运行排产")
        self.export_excel_btn = QPushButton("导出 Excel")
        self.export_pdf_btn = QPushButton("导出 PDF")
        self.balance_btn.clicked.connect(self.balance)
        self.export_excel_btn.clicked.connect(self.export_excel)
        self.export_pdf_btn.clicked.connect(self.export_pdf)
        action_bar.addWidget(self.balance_btn)
        action_bar.addWidget(self.export_excel_btn)
        action_bar.addWidget(self.export_pdf_btn)
        action_bar.addStretch(1)
        root.addLayout(action_bar)

        body = QSplitter(Qt.Orientation.Horizontal)

        employee_group = QGroupBox("排产人员")
        employee_layout = QVBoxLayout(employee_group)
        self.run_employee_search = QLineEdit()
        self.run_employee_search.setPlaceholderText("搜索人员姓名或岗位")
        self.run_employee_search.textChanged.connect(self._refresh_run_employee_list)
        employee_layout.addWidget(self.run_employee_search)
        self.run_employee_list = QListWidget()
        self.run_employee_list.setSelectionMode(QAbstractItemView.MultiSelection)
        employee_layout.addWidget(self.run_employee_list, 1)
        body.addWidget(employee_group)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        self.run_summary_label = QLabel("未选择款式")
        self.run_summary_label.setStyleSheet("font-weight: 600;")
        result_layout.addWidget(self.run_summary_label)
        self.metrics_widget = BalancingMetricsWidget()
        result_layout.addWidget(self.metrics_widget)
        self.result_views = QTabWidget()
        self.overview_widget = BalancingOverviewWidget()
        self.result_views.addTab(self.overview_widget, "模块总览")
        self.layout_view = LayoutGraphicsView()
        self.layout_view.on_distance_changed = self._distance_changed
        self.result_views.addTab(self.layout_view, "工位布局")
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("排产日志和结果会显示在这里。")
        self.result_views.addTab(self.result_text, "文本结果")
        result_layout.addWidget(self.result_views, 4)
        body.addWidget(result_panel)

        body.setSizes([320, 1040])
        root.addWidget(body, 1)
        return page

    def _build_skill_matrix_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        import_bar = QHBoxLayout()
        self.employee_path = QLineEdit("人员技能矩阵.csv")
        browse_btn = QPushButton("选择技能矩阵")
        browse_btn.clicked.connect(lambda: self._browse(self.employee_path, "选择技能矩阵", "CSV (*.csv)"))
        self.import_skill_btn = QPushButton("导入技能矩阵")
        self.import_skill_btn.clicked.connect(self.import_skill_matrix)
        import_bar.addWidget(self.employee_path, 1)
        import_bar.addWidget(browse_btn)
        import_bar.addWidget(self.import_skill_btn)
        root.addLayout(import_bar)

        self.skill_summary_label = QLabel("尚未导入技能矩阵")
        self.skill_summary_label.setStyleSheet("font-weight: 600; color: #20506b;")
        root.addWidget(self.skill_summary_label)

        self.uncovered_summary_label = QLabel("未覆盖工序：0")
        self.uncovered_summary_label.setStyleSheet("font-weight: 600; color: #8a4b00;")
        root.addWidget(self.uncovered_summary_label)

        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        issue_group = QGroupBox("导入校验")
        issue_layout = QVBoxLayout(issue_group)
        self.skill_issue_table = QTableWidget(0, 5)
        self.skill_issue_table.setHorizontalHeaderLabels(["级别", "对象", "行", "字段", "说明"])
        self.skill_issue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        issue_layout.addWidget(self.skill_issue_table)
        top_splitter.addWidget(issue_group)

        employee_group = QGroupBox("人员技能")
        employee_layout = QVBoxLayout(employee_group)
        filter_bar = QHBoxLayout()
        self.employee_search = QLineEdit()
        self.employee_search.setPlaceholderText("搜索员工姓名或岗位")
        self.employee_search.textChanged.connect(self._refresh_employee_list)
        self.low_efficiency_only = QCheckBox("只看低熟练度")
        self.low_efficiency_only.toggled.connect(self._refresh_employee_list)
        filter_bar.addWidget(self.employee_search, 1)
        filter_bar.addWidget(self.low_efficiency_only)
        employee_layout.addLayout(filter_bar)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.employee_list = EmployeeDropListWidget(self._handle_employee_skill_drop)
        self.employee_list.currentItemChanged.connect(self._on_employee_selected)
        content_splitter.addWidget(self.employee_list)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.selected_employee_label = QLabel("请选择员工查看技能。")
        self.selected_employee_label.setStyleSheet("font-weight: 600;")
        right_layout.addWidget(self.selected_employee_label)
        self.employee_skill_table = QTableWidget(0, 5)
        self.employee_skill_table.setHorizontalHeaderLabels(["工序ID", "标准工序", "效率", "来源", "备注"])
        self.employee_skill_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.employee_skill_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.employee_skill_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        right_layout.addWidget(self.employee_skill_table, 1)

        uncovered_group = QGroupBox("待补员工技能的标准工序")
        uncovered_layout = QVBoxLayout(uncovered_group)
        self.uncovered_skill_table = UncoveredSkillTable(0, 4)
        self.uncovered_skill_table.setHorizontalHeaderLabels(["工序ID", "标准工序", "来源", "提醒"])
        self.uncovered_skill_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.uncovered_skill_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        uncovered_layout.addWidget(self.uncovered_skill_table)
        right_layout.addWidget(uncovered_group, 1)

        buttons = QHBoxLayout()
        self.add_skill_btn = QPushButton("新增技能")
        self.edit_skill_btn = QPushButton("修改效率")
        self.delete_skill_btn = QPushButton("删除技能")
        self.refresh_skill_btn = QPushButton("刷新")
        self.add_skill_btn.clicked.connect(self.add_employee_skill)
        self.edit_skill_btn.clicked.connect(self.edit_employee_skill)
        self.delete_skill_btn.clicked.connect(self.delete_employee_skill)
        self.refresh_skill_btn.clicked.connect(self._refresh_skill_matrix_tab)
        for button in [self.add_skill_btn, self.edit_skill_btn, self.delete_skill_btn, self.refresh_skill_btn]:
            buttons.addWidget(button)
        buttons.addStretch(1)
        right_layout.addLayout(buttons)
        content_splitter.addWidget(right_panel)
        content_splitter.setSizes([300, 900])
        employee_layout.addWidget(content_splitter, 1)
        top_splitter.addWidget(employee_group)
        top_splitter.setSizes([420, 980])
        root.addWidget(top_splitter, 1)
        return page

    def _build_worktime_library_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        import_bar = QHBoxLayout()
        self.process_path = QLineEdit("EGLES6423BK_20260418.xls")
        browse_btn = QPushButton("选择工时表")
        browse_btn.clicked.connect(lambda: self._browse(self.process_path, "选择标准工时表", "Excel/CSV (*.xlsx *.xls *.csv)"))
        self.import_style_btn = QPushButton("导入当前工时表")
        self.import_style_btn.clicked.connect(self.import_style_processes)
        import_bar.addWidget(self.process_path, 1)
        import_bar.addWidget(browse_btn)
        import_bar.addWidget(self.import_style_btn)
        root.addLayout(import_bar)

        mapping_bar = QHBoxLayout()
        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 20)
        self.batch_size_spin.setValue(2)
        self.mapping_concurrency_spin = QSpinBox()
        self.mapping_concurrency_spin.setRange(1, 8)
        self.mapping_concurrency_spin.setValue(1)
        self.map_btn = QPushButton("DeepSeek 智能映射")
        self.map_btn.clicked.connect(self.map_current_style)
        self.review_btn = QPushButton("人工确认映射")
        self.review_btn.clicked.connect(self.review_style_mappings)
        self.refresh_styles_btn = QPushButton("刷新款式")
        self.refresh_styles_btn.clicked.connect(self._refresh_reference_data)
        mapping_bar.addWidget(QLabel("每批数量"))
        mapping_bar.addWidget(self.batch_size_spin)
        mapping_bar.addWidget(QLabel("并发批次"))
        mapping_bar.addWidget(self.mapping_concurrency_spin)
        mapping_bar.addWidget(self.map_btn)
        mapping_bar.addWidget(self.review_btn)
        mapping_bar.addWidget(self.refresh_styles_btn)
        mapping_bar.addStretch(1)
        root.addLayout(mapping_bar)

        self.style_summary_label = QLabel("尚未选择款式")
        self.style_summary_label.setStyleSheet("font-weight: 600; color: #20506b;")
        root.addWidget(self.style_summary_label)
        self.style_log_text = QTextEdit()
        self.style_log_text.setReadOnly(True)
        self.style_log_text.setPlaceholderText("工时库导入、DeepSeek 映射和人工确认日志会显示在这里。")
        root.addWidget(self.style_log_text, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_group = QGroupBox("款式")
        left_layout = QVBoxLayout(left_group)
        self.style_list = QListWidget()
        self.style_list.currentItemChanged.connect(self._on_style_selected)
        left_layout.addWidget(self.style_list)
        splitter.addWidget(left_group)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.style_issue_table = QTableWidget(0, 5)
        self.style_issue_table.setHorizontalHeaderLabels(["级别", "对象", "行", "字段", "说明"])
        self.style_issue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        right_layout.addWidget(QLabel("工时导入校验"))
        right_layout.addWidget(self.style_issue_table, 1)

        process_group = QGroupBox("款式工时")
        process_layout = QVBoxLayout(process_group)
        self.style_process_table = QTableWidget(0, 9)
        self.style_process_table.setHorizontalHeaderLabels(
            ["工序号", "部件", "工序描述", "标准时间", "单价", "最终标准工序", "AI候选", "置信度", "原因"]
        )
        self.style_process_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.style_process_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.style_process_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        process_layout.addWidget(self.style_process_table)
        right_layout.addWidget(process_group, 3)

        splitter.addWidget(right_panel)
        splitter.setSizes([260, 1200])
        root.addWidget(splitter, 1)
        return page

    def _browse(self, line_edit: QLineEdit, title: str, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, str(self.settings.app.root_dir), file_filter)
        if path:
            line_edit.setText(path)

    def import_skill_matrix(self) -> None:
        employee_file = self.employee_path.text().strip()
        self._set_busy("正在导入技能矩阵...")
        self._append_runtime_log("开始导入技能矩阵。")

        def task():
            return load_skill_matrix(employee_file, category_id=DEFAULT_CATEGORY_ID)

        self._run_task(task, self._on_skill_matrix_imported)

    def _on_skill_matrix_imported(self, payload) -> None:
        employees, skill_processes, employee_skills, issues = payload
        self.skill_issues = issues
        self._render_validation_issues(self.skill_issue_table, issues)
        if blocking_issues(issues):
            self.status_label.setText("技能矩阵导入存在错误，请修正后重试。")
            return
        self.repository.replace_employee_import(
            employees,
            skill_processes,
            employee_skills,
            category_id=DEFAULT_CATEGORY_ID,
        )
        self._append_runtime_log(f"技能矩阵导入完成：{len(employees)} 名员工，{len(employee_skills)} 条技能。")
        self._refresh_reference_data()

    def import_style_processes(self) -> None:
        process_file = self.process_path.text().strip()
        self._set_busy("正在导入标准工时表...")
        self._append_runtime_log("开始导入标准工时表。")

        def task():
            return load_processes(
                process_file,
                self.settings.templates.combination_component,
                category_id=DEFAULT_CATEGORY_ID,
            )

        self._run_task(task, self._on_style_processes_imported)

    def _on_style_processes_imported(self, payload) -> None:
        processes, issues = payload
        self.style_issues = issues
        self._render_validation_issues(self.style_issue_table, issues)
        if blocking_issues(issues) or not processes:
            self.status_label.setText("工时表导入存在错误，请修正后重试。")
            return
        style_no = processes[0].style_no
        self.repository.save_style_processes(processes, category_id=DEFAULT_CATEGORY_ID)
        self._append_runtime_log(f"款式 {style_no} 工时导入完成：{len(processes)} 道工序。")
        self._refresh_reference_data(style_no=style_no)

    def map_current_style(self) -> None:
        style_no = self._current_style_no()
        if not style_no:
            QMessageBox.information(self, "提示", "请先在标准工时库页选择一个款式。")
            return
        processes = self.repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
        if not processes:
            QMessageBox.warning(self, "缺少工时", "当前款式没有工时数据。")
            return
        batch_size = self.batch_size_spin.value()
        concurrency = self.mapping_concurrency_spin.value()
        self._set_busy(f"正在为款式 {style_no} 执行 DeepSeek 映射...")
        self._append_runtime_log(f"开始映射款式 {style_no}，batch={batch_size} concurrency={concurrency}。")
        self._append_style_log(f"开始映射款式 {style_no}，batch={batch_size} concurrency={concurrency}。")

        def task(progress):
            def on_batch(batch_records: list[MappingRecord]) -> None:
                self.repository.upsert_style_mappings(style_no, processes, batch_records)
                progress("__STYLE_REFRESH__")
            return self.mapper.map_processes(
                processes,
                [],
                progress=progress,
                category_id=DEFAULT_CATEGORY_ID,
                batch_size=batch_size,
                max_concurrency=concurrency,
                on_batch=on_batch,
            )

        self._run_task(
            task,
            lambda mappings: self._on_style_mapped(style_no, processes, mappings),
            on_progress=self._handle_style_progress,
        )

    def _on_style_mapped(self, style_no: str, processes: list[Process], mappings: list[MappingRecord]) -> None:
        self._append_runtime_log(f"款式 {style_no} 映射完成：{len(mappings)} 条。")
        self._append_style_log(f"款式 {style_no} 映射完成：{len(mappings)} 条。")
        self._refresh_reference_data(style_no=style_no)

    def review_style_mappings(self) -> None:
        style_no = self._current_style_no()
        if not style_no:
            QMessageBox.information(self, "提示", "请先在标准工时库页选择一个款式。")
            return
        mappings = self.repository.list_style_mappings(style_no)
        if not mappings:
            QMessageBox.information(self, "暂无映射", "当前款式没有可确认的映射记录。")
            return
        dialog = MappingReviewDialog(
            mappings,
            self.repository.list_skill_processes(DEFAULT_CATEGORY_ID, include_inactive=False),
            self.settings.llm.confidence_threshold,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.confirmed_records()
        created_count = 0
        for record in updated:
            process_name = record.final_skill_name.strip()
            if not process_name:
                continue
            existing = self.repository.match_skill_process_by_name(DEFAULT_CATEGORY_ID, process_name)
            process = self.repository.resolve_or_create_skill_process(DEFAULT_CATEGORY_ID, process_name, source="mapping")
            if existing is None:
                created_count += 1
            record.final_process_id = process.id
            record.final_skill_name = process.display_name
            record.human_approved = True
            record.suggested_new_skill = False
        self.repository.save_style_mappings(style_no, self.repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID), updated)
        self._append_runtime_log(f"款式 {style_no} 的人工确认映射已保存。")
        self._append_style_log(f"款式 {style_no} 的人工确认映射已保存。")
        if created_count:
            self._append_runtime_log(
                f"本次新建了 {created_count} 个标准工序，请到技能矩阵页补充员工掌握情况和效率。"
            )
            self._append_style_log(
                f"本次新建了 {created_count} 个标准工序，请到技能矩阵页补充员工掌握情况和效率。"
            )
        self._refresh_reference_data(style_no=style_no)

    def balance(self) -> None:
        style_no = self.run_style_combo.currentText().strip()
        if not style_no:
            QMessageBox.information(self, "提示", "请先选择款式。")
            return
        processes = self.repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
        if not processes:
            QMessageBox.warning(self, "缺少工时", "当前款式没有工时数据。")
            return
        mappings = self.repository.list_style_mappings(style_no)
        if len(mappings) != len(processes):
            QMessageBox.warning(self, "映射未完成", "当前款式尚未完成工序映射，请先到标准工时库页处理。")
            return
        if not is_human_confirmed(mappings, self.settings.llm.confidence_threshold):
            QMessageBox.warning(self, "映射未确认", "当前款式仍有低置信或未确认映射，请先人工确认。")
            return
        employees = self._selected_run_employees()
        if not employees:
            QMessageBox.warning(self, "缺少人员", "请至少选择一名员工。")
            return

        uncovered = self._uncovered_run_processes(processes, mappings, employees)
        if uncovered:
            preview = "、".join(item["name"] for item in uncovered[:5])
            extra = "" if len(uncovered) <= 5 else f" 等 {len(uncovered)} 个工序"
            QMessageBox.warning(
                self,
                "员工技能未覆盖",
                f"以下最终标准工序当前没有任何已选员工掌握：{preview}{extra}。请先到技能矩阵页维护技能后再排产。",
            )
            return

        self._set_busy(f"正在为款式 {style_no} 运行排产...")
        self._append_runtime_log(f"开始运行款式 {style_no} 的排产。")
        self.style_processes = processes
        self.style_mappings = mappings
        self._run_task(lambda: self.balancer.balance(processes, employees, mappings), self._on_balanced)

    def _on_balanced(self, result: AssignmentResult) -> None:
        self.result = result
        apply_straight_layout(result.stations, self.settings.layout.station_width, self.settings.layout.grid_gap)
        flows = default_flows(self.style_processes, self.settings.layout.default_flow_volume)
        self.layout_view.render_layout(
            result.stations,
            flows,
            self.settings.layout.station_width,
            self.settings.layout.station_height,
        )
        self.overview_widget.set_result(result, self.style_processes)
        self.result_views.setCurrentWidget(self.overview_widget)
        self.repository.save_layout_result(
            result,
            {record.process_hash: record.final_process_id for record in self.style_mappings},
            result.metrics.total_distance,
        )
        self._render_result_text()
        self.status_label.setText("排产完成。")
        self._append_runtime_log("排产完成。")
        self._update_buttons()

    def _distance_changed(self, distance: float) -> None:
        if not self.result:
            return
        self.result.metrics.total_distance = distance
        self.metrics_widget.set_metrics(self.result.metrics)
        self._render_result_text()

    def export_excel(self) -> None:
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "排产报告.xlsx", "Excel (*.xlsx)")
        if path:
            issues = self.style_issues if self.style_issues else self.skill_issues
            self.exporter.export_excel(path, self.result, self.style_mappings, issues)
            QMessageBox.information(self, "导出完成", f"Excel 已导出：{path}")

    def export_pdf(self) -> None:
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 PDF", "排产报告.pdf", "PDF (*.pdf)")
        if path:
            self.exporter.export_pdf(path, self.result, self.style_mappings)
            QMessageBox.information(self, "导出完成", f"PDF 已导出：{path}")

    def add_employee_skill(self) -> None:
        employee = self._selected_employee()
        if employee is None:
            QMessageBox.information(self, "提示", "请先选择员工。")
            return
        process_names = [process.display_name for process in self.skill_processes if process.is_active]
        if not process_names:
            QMessageBox.warning(self, "缺少标准工序", "当前没有可用的标准工序。")
            return
        process_name, ok = QInputDialog.getItem(self, "新增技能", "标准工序", process_names, 0, False)
        if not ok or not process_name:
            return
        efficiency, ok = QInputDialog.getDouble(self, "新增技能", "效率", 1.0, 0.01, 9.99, 2)
        if not ok:
            return
        process = self.repository.match_skill_process_by_name(DEFAULT_CATEGORY_ID, process_name)
        if not process:
            return
        self._save_employee_skill(employee, process.id, float(efficiency))

    def edit_employee_skill(self) -> None:
        employee = self._selected_employee()
        row = self.employee_skill_table.currentRow()
        if employee is None or row < 0:
            QMessageBox.information(self, "提示", "请先选择一条员工技能记录。")
            return
        process_id_item = self.employee_skill_table.item(row, 0)
        efficiency_item = self.employee_skill_table.item(row, 2)
        if not process_id_item or not efficiency_item:
            return
        current_efficiency = float(efficiency_item.text())
        efficiency, ok = QInputDialog.getDouble(self, "修改效率", "效率", current_efficiency, 0.01, 9.99, 2)
        if not ok:
            return
        self._save_employee_skill(employee, process_id_item.text(), float(efficiency))

    def delete_employee_skill(self) -> None:
        employee = self._selected_employee()
        row = self.employee_skill_table.currentRow()
        if employee is None or row < 0:
            QMessageBox.information(self, "提示", "请先选择一条员工技能记录。")
            return
        process_id_item = self.employee_skill_table.item(row, 0)
        if not process_id_item:
            return
        self.repository.delete_employee_skill(employee.id, DEFAULT_CATEGORY_ID, process_id_item.text())
        self._refresh_reference_data(style_no=self._current_style_no())
        self._on_employee_selected(self.employee_list.currentItem())

    def _handle_employee_skill_drop(self, item: QListWidgetItem, payload: dict[str, str]) -> None:
        employee = self._selected_employee(item)
        process_id = payload.get("process_id", "").strip()
        process_name = payload.get("process_name", "").strip() or process_id
        if employee is None or not process_id:
            return
        existing_efficiency = employee.skills.get(process_id)
        title = "Drag Add Skill"
        prompt = f"Set efficiency for {employee.name} -> {process_name}"
        default_efficiency = 1.0
        if existing_efficiency is not None and existing_efficiency > 0:
            title = "Drag Update Skill"
            prompt = f"{employee.name} already has {process_name}. Set new efficiency"
            default_efficiency = float(existing_efficiency)
        efficiency, ok = QInputDialog.getDouble(self, title, prompt, default_efficiency, 0.01, 9.99, 2)
        if not ok:
            return
        self._save_employee_skill(employee, process_id, float(efficiency))

    def _save_employee_skill(self, employee: Employee, process_id: str, efficiency: float) -> None:
        self.repository.upsert_employee_skill(
            EmployeeSkill(
                employee_id=employee.id,
                category_id=DEFAULT_CATEGORY_ID,
                process_id=process_id,
                efficiency=efficiency,
                source="manual",
            )
        )
        self._refresh_reference_data(style_no=self._current_style_no())
        self._on_employee_selected(self.employee_list.currentItem())

    def _refresh_reference_data(self, style_no: str | None = None) -> None:
        self.skill_processes = self.repository.list_skill_processes(DEFAULT_CATEGORY_ID)
        self.employees = self.repository.load_employees_with_skills(DEFAULT_CATEGORY_ID)
        self.styles = self.repository.list_styles(DEFAULT_CATEGORY_ID)
        self._refresh_style_widgets(style_no=style_no)
        self._refresh_skill_matrix_tab()
        self._refresh_run_widgets(style_no=style_no)
        self._update_buttons()

    def _refresh_style_widgets(self, style_no: str | None = None) -> None:
        current_style = style_no or self._current_style_no()
        self.style_list.blockSignals(True)
        self.style_list.clear()
        for item_style in self.styles:
            item = QListWidgetItem(item_style)
            self.style_list.addItem(item)
            if item_style == current_style:
                self.style_list.setCurrentItem(item)
        self.style_list.blockSignals(False)
        if self.style_list.count() and self.style_list.currentRow() < 0:
            self.style_list.setCurrentRow(0)
        self._load_style_context(self._current_style_no())

    def _refresh_run_widgets(self, style_no: str | None = None) -> None:
        current_style = style_no or self.run_style_combo.currentText().strip()
        self.run_style_combo.blockSignals(True)
        self.run_style_combo.clear()
        self.run_style_combo.addItems(self.styles)
        if current_style and current_style in self.styles:
            self.run_style_combo.setCurrentText(current_style)
        self.run_style_combo.blockSignals(False)
        self._refresh_run_employee_list()
        self._on_run_style_changed(self.run_style_combo.currentText())

    def _load_style_context(self, style_no: str) -> None:
        self.style_processes = self.repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID) if style_no else []
        self.style_mappings = self.repository.list_style_mappings(style_no) if style_no else []
        pending = self.repository.list_style_pending_mappings(style_no) if style_no else []
        self.style_summary_label.setText(
            f"款式：{style_no or '-'}   工序：{len(self.style_processes)}   映射：{len(self.style_mappings)}   待确认：{len(pending)}"
        )
        self._render_style_processes()

    def _refresh_skill_matrix_tab(self) -> None:
        self.uncovered_skill_processes = self.repository.list_uncovered_skill_processes(DEFAULT_CATEGORY_ID)
        self.skill_summary_label.setText(
            f"标准工序：{len(self.skill_processes)}   员工：{len(self.employees)}"
        )
        self.uncovered_summary_label.setText(
            f"未覆盖工序：{len(self.uncovered_skill_processes)}   必须全部至少有一名员工掌握后才能排产"
        )
        self._refresh_employee_list()
        self._refresh_run_employee_list()
        self._render_uncovered_skill_processes()

    def _render_validation_issues(self, table: QTableWidget, issues: list[ValidationIssue]) -> None:
        table.setRowCount(len(issues))
        severity_colors = {
            "error": QColor("#fdecea"),
            "warning": QColor("#fff6dd"),
            "info": QColor("#eef7ff"),
        }
        for row, issue in enumerate(issues):
            values = [
                issue.severity,
                issue.entity,
                "" if issue.row_index is None else str(issue.row_index),
                issue.field,
                issue.message,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(severity_colors.get(issue.severity, QColor("#ffffff")))
                table.setItem(row, col, item)

    def _render_style_processes(self) -> None:
        self.style_process_table.setRowCount(len(self.style_processes))
        mapping_by_hash = {record.process_hash: record for record in self.style_mappings}
        for row, process in enumerate(self.style_processes):
            mapping = mapping_by_hash.get(process.identity_hash)
            values = [
                process.process_no,
                process.component,
                process.description,
                f"{process.standard_time:.2f}",
                f"{process.standard_price:.2f}",
                mapping.final_skill_name if mapping else "",
                mapping.llm_skill_name if mapping else "",
                f"{mapping.confidence:.2f}" if mapping else "",
                mapping.reason if mapping else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if mapping and col >= 5 and (mapping.suggested_new_skill or not mapping.human_approved):
                    item.setBackground(QColor("#fff6dd"))
                self.style_process_table.setItem(row, col, item)

    def _render_uncovered_skill_processes(self) -> None:
        self.uncovered_skill_table.setRowCount(len(self.uncovered_skill_processes))
        for row, process in enumerate(self.uncovered_skill_processes):
            reminder = "尚未设置任何员工会做该工序，效率也未维护"
            if process["source"] == "mapping":
                reminder = "映射新建工序，需补充员工掌握情况和效率"
            values = [
                process["process_id"],
                process["display_name"],
                process["source"],
                reminder,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(QColor("#fff6dd"))
                self.uncovered_skill_table.setItem(row, col, item)

    def _render_result_text(self) -> None:
        if not self.result:
            self.metrics_widget.set_metrics(None)
            self.overview_widget.set_result(None, [])
            self.result_text.clear()
            return
        self.metrics_widget.set_metrics(self.result.metrics)
        lines = [
            f"工位数：{self.result.metrics.num_stations}",
            f"总有效工时：{self.result.metrics.total_effective_time:.2f}",
            "",
        ]
        for station in self.result.stations:
            lines.append(f"{station.station_id} {station.employee_name} | 负荷 {station.load_time:.2f}")
            for process in station.assigned_processes:
                lines.append(
                    f"  {process['process_no']} {process['standard_process_name']} / {process['description']} "
                    f"({process['effective_time']:.2f})"
                )
            lines.append("")
        if self.result.warnings:
            lines.append("提示：")
            lines.extend(f"- {warning}" for warning in self.result.warnings)
        self.result_text.setPlainText("\n".join(lines).strip())

    def _set_busy(self, message: str | None = None) -> None:
        if message:
            self.busy_message = message
            self.busy_started_at = time.time()
            self.busy_timer.start()
            self.status_label.setText(message)
            return
        self.busy_started_at = None
        self.busy_message = ""
        self.busy_timer.stop()

    def _refresh_busy_elapsed(self) -> None:
        if self.busy_started_at is None or not self.busy_message:
            return
        elapsed = int(time.time() - self.busy_started_at)
        self.status_label.setText(f"{self.busy_message}（{elapsed}s）")

    def _append_runtime_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.result_text.append(f"[{stamp}] {message}")

    def _append_style_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.style_log_text.append(f"[{stamp}] {message}")

    def _handle_style_progress(self, message: str) -> None:
        if message == "__STYLE_REFRESH__":
            current_style = self._current_style_no()
            if current_style:
                self._load_style_context(current_style)
            return
        self._append_style_log(message)

    def _run_task(self, fn, on_success, on_progress=None) -> None:
        def handle_success(payload) -> None:
            self._set_busy(None)
            on_success(payload)
            self._update_buttons()

        def handle_error(message: str) -> None:
            self._set_busy(None)
            self._append_runtime_log(message)
            QMessageBox.critical(self, "执行失败", message)
            self._update_buttons()

        progress_handler = on_progress or self._append_runtime_log
        thread = run_in_thread(fn, handle_success, handle_error, progress_handler)
        self.threads.append(thread)
        thread.finished.connect(lambda: self._cleanup_thread(thread))
        self._update_buttons()

    def _cleanup_thread(self, thread) -> None:
        if thread in self.threads:
            self.threads.remove(thread)
        self._update_buttons()

    def _update_buttons(self) -> None:
        busy = bool(self.threads)
        has_style = bool(self.run_style_combo.currentText().strip())
        has_result = self.result is not None
        has_style_selection = bool(self._current_style_no())
        has_style_mappings = bool(self.style_mappings)

        self.refresh_run_btn.setEnabled(not busy)
        self.balance_btn.setEnabled(not busy and has_style)
        self.export_excel_btn.setEnabled(not busy and has_result)
        self.export_pdf_btn.setEnabled(not busy and has_result)

        self.import_skill_btn.setEnabled(not busy)
        self.add_skill_btn.setEnabled(not busy and bool(self.skill_processes))
        self.edit_skill_btn.setEnabled(not busy)
        self.delete_skill_btn.setEnabled(not busy)
        self.refresh_skill_btn.setEnabled(not busy)

        self.import_style_btn.setEnabled(not busy)
        self.map_btn.setEnabled(not busy and has_style_selection)
        self.review_btn.setEnabled(not busy and has_style_mappings)
        self.refresh_styles_btn.setEnabled(not busy)
        self.batch_size_spin.setEnabled(not busy)
        self.mapping_concurrency_spin.setEnabled(not busy)

    def _current_style_no(self) -> str:
        item = self.style_list.currentItem()
        return item.text().strip() if item else ""

    def _on_style_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        style_no = current.text().strip() if current else ""
        self._load_style_context(style_no)
        self._sync_run_style(style_no)
        self._update_buttons()

    def _sync_run_style(self, style_no: str) -> None:
        if style_no and style_no in self.styles:
            self.run_style_combo.setCurrentText(style_no)

    def _on_run_style_changed(self, style_no: str) -> None:
        style_no = style_no.strip()
        if style_no:
            processes = self.repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
            mappings = self.repository.list_style_mappings(style_no)
            pending = self.repository.list_style_pending_mappings(style_no)
            self.run_summary_label.setText(
                f"款式：{style_no}   工序：{len(processes)}   映射：{len(mappings)}   待确认：{len(pending)}"
            )
        else:
            self.run_summary_label.setText("未选择款式")
        self._update_buttons()

    def _refresh_employee_list(self) -> None:
        selected_id = self._selected_employee().id if self._selected_employee() else ""
        keyword = self.employee_search.text().strip().lower()
        low_only = self.low_efficiency_only.isChecked()
        self.employee_list.clear()
        for employee in self.employees:
            skill_values = list(employee.skills.values())
            if keyword and keyword not in employee.name.lower() and keyword not in employee.role.lower():
                continue
            if low_only and not any(value < 0.8 for value in skill_values):
                continue
            item = QListWidgetItem(f"{employee.name}  {employee.role}".strip())
            item.setData(Qt.ItemDataRole.UserRole, employee.id)
            self.employee_list.addItem(item)
            if employee.id == selected_id:
                self.employee_list.setCurrentItem(item)
        if self.employee_list.count() and self.employee_list.currentRow() < 0:
            self.employee_list.setCurrentRow(0)

    def _refresh_run_employee_list(self) -> None:
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.run_employee_list.selectedItems()}
        keyword = self.run_employee_search.text().strip().lower()
        self.run_employee_list.clear()
        for employee in self.employees:
            if keyword and keyword not in employee.name.lower() and keyword not in employee.role.lower():
                continue
            item = QListWidgetItem(f"{employee.name}  {employee.role}".strip())
            item.setData(Qt.ItemDataRole.UserRole, employee.id)
            self.run_employee_list.addItem(item)
            if not selected_ids or employee.id in selected_ids:
                item.setSelected(True)

    def _select_run_employees(self, selected: bool) -> None:
        for index in range(self.run_employee_list.count()):
            self.run_employee_list.item(index).setSelected(selected)

    def _select_all_run_employees(self) -> None:
        self._select_run_employees(True)

    def _selected_run_employees(self) -> list[Employee]:
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.run_employee_list.selectedItems()}
        return [employee for employee in self.employees if employee.id in selected_ids]

    def _on_employee_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        employee = self._selected_employee(current)
        if employee is None:
            self.selected_employee_label.setText("请选择员工查看技能。")
            self.employee_skill_table.setRowCount(0)
            return
        self.selected_employee_label.setText(f"员工：{employee.name}")
        skills = self.repository.list_employee_skills(employee.id, DEFAULT_CATEGORY_ID)
        process_map = {process.id: process.display_name for process in self.skill_processes}
        self.employee_skill_table.setRowCount(len(skills))
        for row, skill in enumerate(skills):
            values = [
                skill.process_id,
                process_map.get(skill.process_id, skill.process_id),
                f"{skill.efficiency:.2f}",
                skill.source,
                skill.notes,
            ]
            for col, value in enumerate(values):
                self.employee_skill_table.setItem(row, col, QTableWidgetItem(value))

    def _selected_employee(self, item: QListWidgetItem | None = None) -> Employee | None:
        current = item or self.employee_list.currentItem()
        if current is None:
            return None
        employee_id = current.data(Qt.ItemDataRole.UserRole)
        return next((employee for employee in self.employees if employee.id == employee_id), None)

    def _uncovered_run_processes(
        self,
        processes: list[Process],
        mappings: list[MappingRecord],
        employees: list[Employee],
    ) -> list[dict[str, str]]:
        mapping_by_hash = {record.process_hash: record for record in mappings}
        uncovered: list[dict[str, str]] = []
        for process in processes:
            mapping = mapping_by_hash.get(process.identity_hash)
            if mapping is None or not mapping.final_process_id:
                uncovered.append({"id": "", "name": mapping.final_skill_name if mapping else process.description})
                continue
            if any(employee.skills.get(mapping.final_process_id, 0.0) > 0 for employee in employees):
                continue
            uncovered.append({"id": mapping.final_process_id, "name": mapping.final_skill_name or process.description})
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in uncovered:
            key = item["id"] or item["name"]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped
