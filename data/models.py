from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


DEFAULT_CATEGORY_ID = "pants"


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    entity: str
    row_index: int | None
    field: str
    message: str


@dataclass(slots=True)
class Category:
    id: str
    code: str
    display_name: str
    is_active: bool = True


@dataclass(slots=True)
class SkillProcess:
    id: str
    category_id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    source: str = "import"
    sort_order: int = 0
    is_active: bool = True


@dataclass(slots=True)
class EmployeeSkill:
    employee_id: str
    category_id: str
    process_id: str
    efficiency: float
    source: str = "import"
    updated_at: str = ""
    notes: str = ""


@dataclass(slots=True)
class Process:
    id: str
    style_no: str
    category_id: str
    component: str
    process_no: str
    description: str
    standard_time: float
    standard_price: float
    sort_order: float
    identity_hash: str
    version_hash: str
    predecessors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Employee:
    id: str
    name: str
    skills: dict[str, float]
    role: str = ""


@dataclass(slots=True)
class MappingRecord:
    # process_hash points back to one style process row; final_process_id points to the normalized skill library row.
    process_hash: str
    process_description: str
    llm_skill_name: str
    final_skill_name: str
    confidence: float
    human_approved: bool
    reason: str = ""
    suggested_new_skill: bool = False
    mes_efficiency: float | None = None
    llm_process_id: str = ""
    final_process_id: str = ""


@dataclass(slots=True)
class Station:
    station_id: str
    employee_id: str
    employee_name: str
    x: float
    y: float
    assigned_processes: list[dict[str, Any]] = field(default_factory=list)
    load_time: float = 0.0


@dataclass(slots=True)
class Flow:
    from_process_id: str
    to_process_id: str
    volume: float = 1.0


@dataclass(slots=True)
class Metrics:
    balance_rate: float
    cycle_time: float
    num_stations: int
    total_effective_time: float
    total_distance: float = 0.0


@dataclass(slots=True)
class AssignmentResult:
    stations: list[Station]
    metrics: Metrics
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LayoutState:
    thread_id: str
    timestamp: datetime
    stage: str
    final_mapping: dict[str, str]
    assignments: dict[str, Any]
    positions: dict[str, tuple[float, float]]
    total_distance: float
    balance_rate: float
