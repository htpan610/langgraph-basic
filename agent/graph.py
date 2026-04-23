from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.nodes import AgentServices, balancing_node, data_ingestion_node, mapping_node
from agent.state import FactoryState
from core.config import Settings


def build_graph(settings: Settings):
    services = AgentServices(settings)
    builder = StateGraph(FactoryState)
    builder.add_node("ingest", lambda state: data_ingestion_node(state, services))
    builder.add_node("mapping", lambda state: mapping_node(state, services))
    builder.add_node("balancing", lambda state: balancing_node(state, services))
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "mapping")
    builder.add_edge("mapping", "balancing")
    builder.add_edge("balancing", END)
    return builder.compile()
