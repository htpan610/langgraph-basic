from __future__ import annotations

from collections import Counter, defaultdict

from core.config import load_settings
from core.db import Repository
from data.models import DEFAULT_CATEGORY_ID


def main() -> None:
    settings = load_settings()
    repository = Repository(settings.app.database_path)
    style_no = repository.list_styles(DEFAULT_CATEGORY_ID)[0]

    processes = repository.load_style_processes(style_no, DEFAULT_CATEGORY_ID)
    mappings = repository.list_style_mappings(style_no)
    employees = repository.load_employees_with_skills(DEFAULT_CATEGORY_ID)
    skill_processes = {item.id: item.display_name for item in repository.list_skill_processes(DEFAULT_CATEGORY_ID)}

    mapped_ids = [item.final_process_id for item in mappings if item.final_process_id]
    coverage: dict[str, list[str]] = defaultdict(list)
    for employee in employees:
        for process_id in mapped_ids:
            if employee.skills.get(process_id, 0.0) > 0:
                coverage[process_id].append(employee.name)

    sparse = []
    for process_id in sorted(set(mapped_ids)):
        names = coverage.get(process_id, [])
        sparse.append((len(names), skill_processes.get(process_id, process_id), names[:8]))
    sparse.sort(key=lambda item: (item[0], item[1]))

    print(f"style={style_no}")
    print("least_covered_processes:")
    for count, name, names in sparse[:20]:
        print(f"  count={count:02d} process={name} sample={names}")

    process_by_hash = {item.process_hash: item for item in mappings}
    unresolved_employees = Counter()
    missing_by_process = Counter()
    for process in processes:
        mapping = process_by_hash[process.identity_hash]
        process_id = mapping.final_process_id
        for employee in employees:
            if employee.skills.get(process_id, 0.0) <= 0:
                unresolved_employees[employee.name] += 1
                missing_by_process[skill_processes.get(process_id, process.description)] += 1

    print("employees_with_most_missing_processes:")
    for name, count in unresolved_employees.most_common(15):
        print(f"  {name}: {count}")

    print("processes_missing_for_most_employees:")
    for name, count in missing_by_process.most_common(20):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
