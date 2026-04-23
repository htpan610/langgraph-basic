from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from core.hash_utils import process_identity_hash, process_version_hash, skill_process_id
from data.models import (
    DEFAULT_CATEGORY_ID,
    Employee,
    EmployeeSkill,
    Process,
    SkillProcess,
    ValidationIssue,
)


PROCESS_COLUMNS = ["款式编号", "部件", "工序号", "工序描述", "标准时间", "标准单价"]
EMPLOYEE_NAME_CANDIDATES = ["姓名", "员工", "员工姓名", "名称"]
EMPLOYEE_META_COLUMNS = {"姓名", "员工", "员工姓名", "名称", "岗位", "距离", "掌握技能数量", "掌握技能数"}


class IngestionError(RuntimeError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("导入数据未通过校验")


def _read_process_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    return pd.read_csv(path, encoding="utf-8")


def is_blank_efficiency(value: object) -> bool:
    if value is None or pd.isna(value):
        return True
    return not str(value).strip()


def parse_efficiency(value: object) -> float | None:
    if is_blank_efficiency(value):
        return None
    text = str(value).strip()
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100.0
        # The import accepts both ratios and percentage-like whole numbers from spreadsheets.
        number = float(text)
        if number > 1.5 and number <= 150:
            return number / 100.0
        return number
    except ValueError:
        return None


def load_processes(
    path: str | Path,
    combination_component: str = "组合",
    category_id: str = DEFAULT_CATEGORY_ID,
) -> tuple[list[Process], list[ValidationIssue]]:
    file_path = Path(path)
    df = _read_process_file(file_path)
    issues: list[ValidationIssue] = []

    missing = [col for col in PROCESS_COLUMNS if col not in df.columns]
    for col in missing:
        issues.append(ValidationIssue("error", "process", None, col, f"工时表缺少必填列：{col}"))
    if missing:
        return [], issues

    duplicate_keys: set[tuple[str, str]] = set()
    seen_keys: set[tuple[str, str]] = set()
    processes: list[Process] = []

    for idx, row in df.iterrows():
        row_no = int(idx) + 2
        style_no = str(row["款式编号"]).strip()
        component = str(row["部件"]).strip()
        process_no = str(row["工序号"]).strip()
        description = str(row["工序描述"]).strip()
        key = (style_no, process_no)
        if key in seen_keys:
            duplicate_keys.add(key)
            issues.append(ValidationIssue("error", "process", row_no, "工序号", f"重复工序：{style_no}/{process_no}"))
        seen_keys.add(key)

        standard_time = parse_efficiency(row["标准时间"])
        if standard_time is None or standard_time <= 0:
            issues.append(ValidationIssue("error", "process", row_no, "标准时间", "标准时间必须是大于0的分钟数"))
            standard_time = 0.0

        try:
            standard_price = float(row["标准单价"])
        except (TypeError, ValueError):
            issues.append(ValidationIssue("warning", "process", row_no, "标准单价", "标准单价不是有效数字，已按0处理"))
            standard_price = 0.0

        try:
            sort_order = float(process_no)
        except ValueError:
            sort_order = float(idx)

        identity = process_identity_hash(style_no, component, process_no, description)
        # version_hash changes when timing/pricing/order changes, while identity_hash tracks the logical process row.
        version = process_version_hash(identity, standard_time, standard_price, sort_order)
        processes.append(
            Process(
                id=f"{style_no}:{process_no}",
                style_no=style_no,
                category_id=category_id,
                component=component,
                process_no=process_no,
                description=description,
                standard_time=float(standard_time),
                standard_price=float(standard_price),
                sort_order=sort_order,
                identity_hash=identity,
                version_hash=version,
            )
        )

    _assign_predecessors(processes, combination_component)
    if duplicate_keys:
        processes = [p for p in processes if (p.style_no, p.process_no) not in duplicate_keys]
    return processes, issues


def _assign_predecessors(processes: list[Process], combination_component: str) -> None:
    by_component: dict[str, list[Process]] = {}
    for process in processes:
        by_component.setdefault(process.component, []).append(process)

    for component_processes in by_component.values():
        component_processes.sort(key=lambda item: item.sort_order)
        for current, previous in zip(component_processes[1:], component_processes):
            current.predecessors.append(previous.id)

    combination = by_component.get(combination_component, [])
    if not combination:
        return
    # Combination/assembly work waits for every non-combination component chain to finish first.
    first_combination = sorted(combination, key=lambda item: item.sort_order)[0]
    for process in processes:
        if process.component != combination_component:
            first_combination.predecessors.append(process.id)


def load_skill_matrix(
    path: str | Path,
    category_id: str = DEFAULT_CATEGORY_ID,
) -> tuple[list[Employee], list[SkillProcess], list[EmployeeSkill], list[ValidationIssue]]:
    file_path = Path(path)
    df = pd.read_csv(file_path, encoding="utf-8")
    issues: list[ValidationIssue] = []
    name_col = next((col for col in EMPLOYEE_NAME_CANDIDATES if col in df.columns), None)
    if not name_col:
        return [], [], [], [ValidationIssue("error", "employee", None, "姓名", "技能矩阵缺少姓名列")]

    skill_columns = [col for col in df.columns if col not in EMPLOYEE_META_COLUMNS]
    skill_processes = [
        SkillProcess(
            id=skill_process_id(category_id, column),
            category_id=category_id,
            display_name=str(column).strip(),
            aliases=[str(column).strip()],
            source="import",
            sort_order=index,
            is_active=True,
        )
        for index, column in enumerate(skill_columns)
    ]
    # Skill matrix headers define the canonical imported process library for employee capability data.
    process_map = {process.display_name: process for process in skill_processes}

    employees: list[Employee] = []
    employee_skills: list[EmployeeSkill] = []
    seen_names: set[str] = set()

    for idx, row in df.iterrows():
        row_no = int(idx) + 2
        name = str(row[name_col]).strip()
        if not name:
            issues.append(ValidationIssue("error", "employee", row_no, name_col, "员工姓名不能为空"))
            continue
        if name in seen_names:
            issues.append(ValidationIssue("error", "employee", row_no, name_col, f"重复员工：{name}"))
            continue
        seen_names.add(name)

        runtime_skills: dict[str, float] = {}
        for column in skill_columns:
            raw_value = row[column]
            if is_blank_efficiency(raw_value):
                continue
            efficiency = parse_efficiency(raw_value)
            if efficiency is None:
                issues.append(ValidationIssue("warning", "employee", row_no, column, f"{name} 的技能效率不是有效数字，已忽略"))
                continue
            if efficiency <= 0:
                continue
            if efficiency > 1.5:
                issues.append(ValidationIssue("warning", "employee", row_no, column, f"{name} 的效率超出 0-1.5 范围，已保留但需要复核"))
            process_id = process_map[str(column).strip()].id
            runtime_skills[process_id] = float(efficiency)
            employee_skills.append(
                EmployeeSkill(
                    employee_id=name,
                    category_id=category_id,
                    process_id=process_id,
                    efficiency=float(efficiency),
                    source="import",
                )
            )

        employees.append(Employee(id=name, name=name, skills=runtime_skills, role=str(row.get("岗位", "")).strip()))

    return employees, skill_processes, employee_skills, issues


def load_employees(path: str | Path) -> tuple[list[Employee], list[ValidationIssue]]:
    employees, _, _, issues = load_skill_matrix(path, category_id=DEFAULT_CATEGORY_ID)
    return employees, issues


def blocking_issues(issues: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    return [issue for issue in issues if issue.severity == "error"]
