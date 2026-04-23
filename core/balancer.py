from __future__ import annotations

import pulp

from core.config import BalancingSettings
from core.db import Repository
from data.models import AssignmentResult, Employee, MappingRecord, Metrics, Process, Station


class BalancingError(RuntimeError):
    pass


class PulpBalancer:
    def __init__(self, settings: BalancingSettings, repository: Repository | None = None) -> None:
        self.settings = settings
        self.repository = repository

    def balance(
        self,
        processes: list[Process],
        employees: list[Employee],
        mappings: list[MappingRecord],
    ) -> AssignmentResult:
        if not processes:
            raise BalancingError("没有可排产的工序")
        if not employees:
            raise BalancingError("没有可用员工")
        if len(employees) > len(processes):
            raise BalancingError("员工数大于工序数，当前规则不允许员工空闲")

        mapping_by_hash = {record.process_hash: record for record in mappings}
        process_name_by_id = {}
        if self.repository:
            process_name_by_id = {item.id: item.display_name for item in self.repository.list_skill_processes()}

        n_processes = len(processes)
        n_employees = len(employees)
        effective_time: dict[tuple[int, int], float] = {}
        warnings: list[str] = []

        for i, process in enumerate(processes):
            mapping = mapping_by_hash.get(process.identity_hash)
            process_id = mapping.final_process_id if mapping else ""
            process_name = mapping.final_skill_name if mapping and mapping.final_skill_name else process.description
            for s, employee in enumerate(employees):
                efficiency = employee.skills.get(process_id, 0.0)
                if efficiency <= 0:
                    efficiency = self.settings.default_missing_efficiency
                    warnings.append(f"{employee.name} 未掌握裤子工序 {process_name}，按默认效率 {efficiency:.2f} 计算")
                effective_time[(i, s)] = process.standard_time / efficiency

        if n_processes * n_employees > 800:
            warnings.append("问题规模较大，已跳过精确 CBC 模型并使用快速平衡求解器。")
            return self._greedy_balance(processes, employees, mappings, effective_time, warnings, process_name_by_id)

        prob = pulp.LpProblem("pants_line_balancing", pulp.LpMinimize)
        x = pulp.LpVariable.dicts("x", ((i, s) for i in range(n_processes) for s in range(n_employees)), cat="Binary")
        cycle_time = pulp.LpVariable("cycle_time", lowBound=0.001)

        prob += cycle_time
        for i in range(n_processes):
            prob += pulp.lpSum(x[i, s] for s in range(n_employees)) == 1
        for s in range(n_employees):
            prob += pulp.lpSum(x[i, s] for i in range(n_processes)) >= 1
            prob += pulp.lpSum(effective_time[(i, s)] * x[i, s] for i in range(n_processes)) <= cycle_time

        index_by_id = {process.id: i for i, process in enumerate(processes)}
        for later_index, process in enumerate(processes):
            for predecessor_id in process.predecessors:
                predecessor_index = index_by_id.get(predecessor_id)
                if predecessor_index is None or predecessor_index == later_index:
                    continue
                prob += (
                    pulp.lpSum(s * x[predecessor_index, s] for s in range(n_employees))
                    <= pulp.lpSum(s * x[later_index, s] for s in range(n_employees))
                )

        status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=self.settings.solver_time_limit_seconds))
        if pulp.LpStatus[status] not in {"Optimal", "Not Solved", "Integer Feasible"}:
            return self._greedy_balance(processes, employees, mappings, effective_time, warnings, process_name_by_id)

        cycle = float(pulp.value(cycle_time) or 0)
        stations: list[Station] = []
        total_effective = 0.0
        for s, employee in enumerate(employees):
            assigned: list[dict] = []
            load = 0.0
            for i, process in enumerate(processes):
                value = x[i, s].value() or 0
                if value <= 0.5:
                    continue
                mapping = mapping_by_hash.get(process.identity_hash)
                process_id = mapping.final_process_id if mapping else ""
                process_name = process_name_by_id.get(process_id, mapping.final_skill_name if mapping else process.description)
                time_value = effective_time[(i, s)]
                load += time_value
                assigned.append(
                    {
                        "process_id": process.id,
                        "process_no": process.process_no,
                        "description": process.description,
                        "component": process.component,
                        "standard_process_id": process_id,
                        "standard_process_name": process_name,
                        "standard_time": process.standard_time,
                        "effective_time": round(time_value, 4),
                    }
                )
            total_effective += load
            stations.append(
                Station(
                    station_id=f"S{s + 1:02d}",
                    employee_id=employee.id,
                    employee_name=employee.name,
                    x=0.0,
                    y=0.0,
                    assigned_processes=assigned,
                    load_time=round(load, 4),
                )
            )

        if not all(station.assigned_processes for station in stations):
            return self._greedy_balance(processes, employees, mappings, effective_time, warnings, process_name_by_id)

        balance_rate = total_effective / (n_employees * cycle) if cycle > 0 else 0.0
        return AssignmentResult(
            stations=stations,
            metrics=Metrics(
                balance_rate=balance_rate,
                cycle_time=cycle,
                num_stations=n_employees,
                total_effective_time=total_effective,
            ),
            warnings=sorted(set(warnings)),
        )

    def _greedy_balance(
        self,
        processes: list[Process],
        employees: list[Employee],
        mappings: list[MappingRecord],
        effective_time: dict[tuple[int, int], float],
        warnings: list[str],
        process_name_by_id: dict[str, str],
    ) -> AssignmentResult:
        mapping_by_hash = {record.process_hash: record for record in mappings}
        stations = [
            Station(
                station_id=f"S{s + 1:02d}",
                employee_id=employee.id,
                employee_name=employee.name,
                x=0.0,
                y=0.0,
            )
            for s, employee in enumerate(employees)
        ]
        loads = [0.0 for _ in stations]
        process_order = _topological_order(processes)

        for index, process in process_order:
            best_station_index = min(range(len(stations)), key=lambda s: loads[s] + effective_time[(index, s)])
            mapping = mapping_by_hash.get(process.identity_hash)
            process_id = mapping.final_process_id if mapping else ""
            process_name = process_name_by_id.get(process_id, mapping.final_skill_name if mapping else process.description)
            self._assign_to_station(
                stations[best_station_index],
                process,
                process_id,
                process_name,
                effective_time[(index, best_station_index)],
            )
            loads[best_station_index] += effective_time[(index, best_station_index)]

        empty_stations = [idx for idx, station in enumerate(stations) if not station.assigned_processes]
        for empty_idx in empty_stations:
            donor_idx = max(range(len(stations)), key=lambda idx: len(stations[idx].assigned_processes))
            if len(stations[donor_idx].assigned_processes) <= 1:
                raise BalancingError("员工数大于可拆分工序数，无法满足不允许空闲规则")
            moved = stations[donor_idx].assigned_processes.pop()
            moved_time = float(moved["effective_time"])
            loads[donor_idx] -= moved_time
            stations[empty_idx].assigned_processes.append(moved)
            loads[empty_idx] += moved_time

        for idx, station in enumerate(stations):
            station.load_time = round(loads[idx], 4)
        cycle = max(loads) if loads else 0.0
        total_effective = sum(loads)
        balance_rate = total_effective / (len(stations) * cycle) if cycle > 0 else 0.0
        warnings.append("CBC 在时限内未返回完整可用解，已使用确定性贪心平衡兜底。")
        return AssignmentResult(
            stations=stations,
            metrics=Metrics(
                balance_rate=balance_rate,
                cycle_time=cycle,
                num_stations=len(stations),
                total_effective_time=total_effective,
            ),
            warnings=sorted(set(warnings)),
        )

    @staticmethod
    def _assign_to_station(
        station: Station,
        process: Process,
        process_id: str,
        process_name: str,
        effective_time: float,
    ) -> None:
        station.assigned_processes.append(
            {
                "process_id": process.id,
                "process_no": process.process_no,
                "description": process.description,
                "component": process.component,
                "standard_process_id": process_id,
                "standard_process_name": process_name,
                "standard_time": process.standard_time,
                "effective_time": round(effective_time, 4),
            }
        )


def result_to_rows(result: AssignmentResult) -> list[dict]:
    rows: list[dict] = []
    for station in result.stations:
        for process in station.assigned_processes:
            rows.append(
                {
                    "工位": station.station_id,
                    "员工": station.employee_name,
                    "工位负荷": station.load_time,
                    **process,
                }
            )
    return rows


def _topological_order(processes: list[Process]) -> list[tuple[int, Process]]:
    remaining = {process.id for process in processes}
    by_id = {process.id: (index, process) for index, process in enumerate(processes)}
    ordered: list[tuple[int, Process]] = []
    while remaining:
        ready = [
            by_id[process_id]
            for process_id in remaining
            if all(pred not in remaining for pred in by_id[process_id][1].predecessors)
        ]
        if not ready:
            ready = [by_id[process_id] for process_id in remaining]
        ready.sort(key=lambda item: (item[1].component == "组合", item[1].component, item[1].sort_order))
        index, process = ready[0]
        ordered.append((index, process))
        remaining.remove(process.id)
    return ordered
