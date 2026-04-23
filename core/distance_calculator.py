from __future__ import annotations

from data.models import Flow, Process, Station


def default_flows(processes: list[Process], volume: float = 1.0) -> list[Flow]:
    ordered = sorted(processes, key=lambda item: (item.component == "组合", item.component, item.sort_order))
    flows: list[Flow] = []
    for previous, current in zip(ordered, ordered[1:]):
        flows.append(Flow(from_process_id=previous.id, to_process_id=current.id, volume=volume))
    return flows


def total_manhattan_distance(stations: list[Station], flows: list[Flow]) -> float:
    process_station: dict[str, Station] = {}
    for station in stations:
        for process in station.assigned_processes:
            process_station[str(process["process_id"])] = station

    total = 0.0
    for flow in flows:
        source = process_station.get(flow.from_process_id)
        target = process_station.get(flow.to_process_id)
        if source is None or target is None:
            continue
        total += (abs(source.x - target.x) + abs(source.y - target.y)) * flow.volume
    return total


def apply_straight_layout(stations: list[Station], width: int, gap: int) -> None:
    for index, station in enumerate(stations):
        station.x = index * (width + gap)
        station.y = 0.0


def apply_u_layout(stations: list[Station], width: int, height: int, gap: int) -> None:
    midpoint = (len(stations) + 1) // 2
    for index, station in enumerate(stations):
        if index < midpoint:
            station.x = index * (width + gap)
            station.y = 0.0
        else:
            reverse_index = len(stations) - index - 1
            station.x = reverse_index * (width + gap)
            station.y = height + gap * 2
