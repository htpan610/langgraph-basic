from __future__ import annotations

import argparse

from core.config import load_settings
from core.db import Repository
from data.models import DEFAULT_CATEGORY_ID, Employee, EmployeeSkill


def _employee_rank(employee: Employee, mapped_ids: set[str]) -> tuple[int, int, str]:
    covered = sum(1 for process_id in mapped_ids if employee.skills.get(process_id, 0.0) > 0)
    return (covered, len(employee.skills), employee.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", help="Style number to enrich. Defaults to the first available style.")
    parser.add_argument("--team-size", type=int, default=12, help="Number of core employees to enrich.")
    parser.add_argument("--efficiency", type=float, default=0.85, help="Efficiency used for newly added skills.")
    args = parser.parse_args()

    settings = load_settings()
    repository = Repository(settings.app.database_path)
    styles = repository.list_styles(DEFAULT_CATEGORY_ID)
    if not styles:
        raise SystemExit("No styles found in the database.")

    style_no = args.style or styles[0]
    mappings = repository.list_style_mappings(style_no)
    mapped_ids = {item.final_process_id for item in mappings if item.final_process_id}
    employees = repository.load_employees_with_skills(DEFAULT_CATEGORY_ID)
    if not mapped_ids or not employees:
        raise SystemExit("Missing mapped processes or employees.")

    ranked = sorted(employees, key=lambda item: _employee_rank(item, mapped_ids), reverse=True)
    core_team = ranked[: min(args.team_size, len(ranked))]

    inserted = 0
    for employee in core_team:
        for process_id in mapped_ids:
            if employee.skills.get(process_id, 0.0) > 0:
                continue
            repository.upsert_employee_skill(
                EmployeeSkill(
                    employee_id=employee.id,
                    category_id=DEFAULT_CATEGORY_ID,
                    process_id=process_id,
                    efficiency=args.efficiency,
                    source="script",
                    notes=f"Second-round enrichment for style {style_no}",
                )
            )
            employee.skills[process_id] = args.efficiency
            inserted += 1

    print(f"style={style_no}")
    print(f"team_size={len(core_team)}")
    print(f"inserted_skills={inserted}")
    print("core_team=", [employee.name for employee in core_team])


if __name__ == "__main__":
    main()
