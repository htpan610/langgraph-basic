from __future__ import annotations

import argparse

from core.balancer import PulpBalancer
from core.config import load_settings
from core.db import Repository
from core.distance_calculator import apply_straight_layout, default_flows, total_manhattan_distance
from core.mapper import is_human_confirmed
from data.models import DEFAULT_CATEGORY_ID, Employee


def _select_employees(process_ids: set[str], employees: list[Employee], cap: int) -> list[Employee]:
    selected_ids: set[str] = set()

    for process_id in process_ids:
        covering = [employee for employee in employees if employee.skills.get(process_id, 0.0) > 0]
        if not covering:
            raise RuntimeError(f"No employee covers required process {process_id}")
        best = max(covering, key=lambda employee: (employee.skills.get(process_id, 0.0), len(employee.skills), employee.name))
        selected_ids.add(best.id)

    ranked = sorted(
        employees,
        key=lambda employee: (
            sum(1 for process_id in process_ids if employee.skills.get(process_id, 0.0) > 0),
            len(employee.skills),
            employee.name,
        ),
        reverse=True,
    )
    for employee in ranked:
        if len(selected_ids) >= cap:
            break
        selected_ids.add(employee.id)

    return [employee for employee in ranked if employee.id in selected_ids][:cap]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", help="Style number to balance. Defaults to the first style in the database.")
    parser.add_argument("--team-size", type=int, help="Optional number of employees to include in balancing.")
    args = parser.parse_args()

    settings = load_settings()
    repository = Repository(settings.app.database_path)
    balancer = PulpBalancer(settings.balancing, repository=repository)

    styles = repository.list_styles(DEFAULT_CATEGORY_ID)
    if not styles:
        raise SystemExit("No styles found.")

    style_no = args.style or styles[0]
    processes = repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
    mappings = repository.list_style_mappings(style_no)
    if not is_human_confirmed(mappings, settings.llm.confidence_threshold):
        raise SystemExit(f"Style {style_no} is still not human confirmed.")

    employees = repository.load_employees_with_skills(DEFAULT_CATEGORY_ID)
    process_ids = {item.final_process_id for item in mappings if item.final_process_id}
    cap = min(len(processes), len(employees))
    if args.team_size is not None:
        cap = min(cap, max(1, args.team_size))
    selected = _select_employees(process_ids, employees, cap=cap)
    if len(selected) > len(processes):
        selected = selected[: len(processes)]

    result = balancer.balance(processes, selected, mappings)
    apply_straight_layout(result.stations, settings.layout.station_width, settings.layout.grid_gap)
    flows = default_flows(processes, settings.layout.default_flow_volume)
    distance = total_manhattan_distance(result.stations, flows)
    result.metrics.total_distance = distance
    repository.save_layout_result(result, {item.process_hash: item.final_skill_name for item in mappings}, distance)

    print(f"style={style_no}")
    print(f"selected_employees={len(selected)}")
    print(f"balance_rate={result.metrics.balance_rate:.4f}")
    print(f"cycle_time={result.metrics.cycle_time:.4f}")
    print(f"num_stations={result.metrics.num_stations}")
    print(f"total_effective_time={result.metrics.total_effective_time:.4f}")
    print(f"total_distance={result.metrics.total_distance:.4f}")
    print(f"warnings={len(result.warnings)}")
    for station in result.stations[:10]:
        print(
            f"station={station.station_id} employee={station.employee_name} "
            f"load={station.load_time:.4f} process_count={len(station.assigned_processes)}"
        )


if __name__ == "__main__":
    main()
