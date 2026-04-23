from __future__ import annotations

import argparse

from core.config import load_settings
from core.db import Repository
from core.mapper import is_human_confirmed
from data.models import DEFAULT_CATEGORY_ID, EmployeeSkill


def _ensure_mapping_process_ids(repository: Repository, style_no: str) -> tuple[int, int]:
    processes = repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
    mappings = repository.list_style_mappings(style_no)
    created_count = 0
    updated_count = 0
    existing_ids = {item.id for item in repository.list_skill_processes(DEFAULT_CATEGORY_ID, include_inactive=True)}

    for record in mappings:
        final_name = (
            record.final_skill_name.strip()
            or record.llm_skill_name.strip()
            or record.process_description.strip()
        )
        if not final_name:
            continue

        process = repository.resolve_or_create_skill_process(
            DEFAULT_CATEGORY_ID,
            final_name,
            source="mapping",
        )
        if process.id not in existing_ids:
            created_count += 1
            existing_ids.add(process.id)
        if not record.final_process_id:
            updated_count += 1
        record.final_process_id = process.id
        record.final_skill_name = process.display_name
        record.human_approved = True
        record.suggested_new_skill = False

    repository.save_style_mappings(style_no, processes, mappings)
    return updated_count, created_count


def _ensure_selected_employees_cover_all_mapped_processes(
    repository: Repository,
    style_no: str,
    efficiency: float,
) -> tuple[list[str], int]:
    mappings = repository.list_style_mappings(style_no)
    employees = repository.load_employees_with_skills(DEFAULT_CATEGORY_ID)
    process_ids = sorted({item.final_process_id for item in mappings if item.final_process_id})
    if not employees or not process_ids:
        return [], 0

    inserted = 0
    selected_employee_ids: set[str] = set()
    coverage = {process_id: [] for process_id in process_ids}
    for employee in employees:
        for process_id in process_ids:
            if employee.skills.get(process_id, 0.0) > 0:
                coverage[process_id].append(employee.id)

    # For any uncovered mapped process, seed it onto the least-loaded employee.
    for process_id, employee_ids in coverage.items():
        if employee_ids:
            selected_employee_ids.add(employee_ids[0])
            continue
        employee = min(employees, key=lambda item: len(item.skills))
        repository.upsert_employee_skill(
            EmployeeSkill(
                employee_id=employee.id,
                category_id=DEFAULT_CATEGORY_ID,
                process_id=process_id,
                efficiency=efficiency,
                source="script",
                notes=f"Auto-added for style {style_no} balancing prep",
            )
        )
        employee.skills[process_id] = efficiency
        selected_employee_ids.add(employee.id)
        inserted += 1

    # Expand the candidate set with the most-skilled employees, but never exceed process count.
    employees = repository.load_employees_with_skills(DEFAULT_CATEGORY_ID)
    cap = min(len(process_ids), len(employees))
    ranked = sorted(employees, key=lambda item: (len(item.skills), item.name), reverse=True)
    for employee in ranked:
        if len(selected_employee_ids) >= cap:
            break
        selected_employee_ids.add(employee.id)

    selected = [employee.id for employee in ranked if employee.id in selected_employee_ids]
    return selected[:cap], inserted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", help="Style number to prepare. Defaults to the first available style.")
    parser.add_argument("--efficiency", type=float, default=1.0, help="Efficiency used for auto-added skills.")
    args = parser.parse_args()

    settings = load_settings()
    repository = Repository(settings.app.database_path)
    styles = repository.list_styles(DEFAULT_CATEGORY_ID)
    if not styles:
        raise SystemExit("No styles found in the database.")

    style_no = args.style or styles[0]
    updated_count, created_count = _ensure_mapping_process_ids(repository, style_no)
    selected_employee_ids, inserted_skills = _ensure_selected_employees_cover_all_mapped_processes(
        repository,
        style_no,
        args.efficiency,
    )
    mappings = repository.list_style_mappings(style_no)

    print(f"style={style_no}")
    print(f"updated_mapping_process_ids={updated_count}")
    print(f"created_or_reused_mapping_processes={created_count}")
    print(f"auto_inserted_employee_skills={inserted_skills}")
    print(f"selected_employee_ids={selected_employee_ids}")
    print(f"human_confirmed={is_human_confirmed(mappings, settings.llm.confidence_threshold)}")


if __name__ == "__main__":
    main()
