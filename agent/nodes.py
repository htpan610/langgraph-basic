from __future__ import annotations

from core.balancer import PulpBalancer
from core.config import Settings
from core.db import Repository
from core.distance_calculator import apply_straight_layout, default_flows, total_manhattan_distance
from core.ingestion import blocking_issues, load_employees, load_processes
from core.llm import DeepSeekProvider
from core.mapper import ProcessMapper, is_human_confirmed


class AgentServices:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = Repository(settings.app.database_path)
        self.llm = DeepSeekProvider(settings.llm)
        self.mapper = ProcessMapper(self.repository, self.llm, settings.llm)
        self.balancer = PulpBalancer(settings.balancing)


def data_ingestion_node(state: dict, services: AgentServices) -> dict:
    processes, p_issues = load_processes(state["process_file"], services.settings.templates.combination_component)
    employees, e_issues = load_employees(state["employee_file"])
    issues = p_issues + e_issues
    if blocking_issues(issues):
        return {**state, "processes": processes, "employees": employees, "issues": issues, "status": "import_failed"}
    services.repository.save_import(processes, employees)
    return {**state, "processes": processes, "employees": employees, "issues": issues, "status": "imported"}


def mapping_node(state: dict, services: AgentServices) -> dict:
    mappings = services.mapper.map_processes(state["processes"], state["employees"])
    for record in mappings:
        services.repository.save_mapping(record)
    confirmed = is_human_confirmed(mappings, services.settings.llm.confidence_threshold)
    return {**state, "mappings": mappings, "human_confirmed": confirmed, "status": "mapped"}


def balancing_node(state: dict, services: AgentServices) -> dict:
    if not state.get("human_confirmed"):
        return {**state, "status": "waiting_human_confirmation"}
    result = services.balancer.balance(state["processes"], state["employees"], state["mappings"])
    apply_straight_layout(
        result.stations,
        services.settings.layout.station_width,
        services.settings.layout.grid_gap,
    )
    flows = default_flows(state["processes"], services.settings.layout.default_flow_volume)
    distance = total_manhattan_distance(result.stations, flows)
    result.metrics.total_distance = distance
    services.repository.save_layout_result(
        result,
        {record.process_hash: record.final_skill_name for record in state["mappings"]},
        distance,
    )
    return {**state, "assignments": result, "total_distance": distance, "status": "balanced"}
