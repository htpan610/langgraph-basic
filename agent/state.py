from __future__ import annotations

from typing import Any, TypedDict


class FactoryState(TypedDict, total=False):
    process_file: str
    employee_file: str
    processes: list[Any]
    employees: list[Any]
    mappings: list[Any]
    human_confirmed: bool
    assignments: Any
    total_distance: float
    status: str
    errors: list[str]
