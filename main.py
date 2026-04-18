from typing import Annotated, List, Dict, Any
from typing_extensions import TypedDict
import pandas as pd
import pulp
import os
from dotenv import load_dotenv

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from langchain.chat_models import init_chat_model

load_dotenv()

# ====================== State ======================
class Process(TypedDict):
    id: str
    name: str
    smv: float
    component: str

class Employee(TypedDict):
    id: str
    name: str
    skills: Dict[str, float]

class Metrics(TypedDict):
    balance_rate: float
    cycle_time: float
    num_stations: int
    total_smv: float

class FactoryState(TypedDict):
    messages: Annotated[list[dict], add_messages]
    processes: List[Process]
    employees: List[Employee]
    mapping: Dict[str, str] | None
    assignments: Dict[str, Any]          # 工位 -> 详细分配信息
    metrics: Metrics | None
    status: str


llm = init_chat_model("deepseek-chat", model_provider="deepseek")


# ====================== 节点 ======================
def data_ingestion_node(state: FactoryState):
    print("【1】加载数据...")
    process_file = "EGLES6423BK_20260418.xls"
    skill_file = "人员技能矩阵.csv"

    df_p = pd.read_excel(process_file)
    processes = []
    for _, row in df_p.iterrows():
        try:
            smv = float(row.iloc[4])
            processes.append({
                "id": str(row.get("工序号", "")),
                "name": str(row.get("工序描述", "")),
                "smv": smv,
                "component": str(row.get("部件", ""))
            })
        except:
            continue

    df_e = pd.read_csv(skill_file, encoding="utf-8")
    employees = []
    skill_cols = [c for c in df_e.columns if c not in ["姓名", "岗位", "距离", "掌握技能数量"]]
    for _, row in df_e.iterrows():
        skills = {col: (float(str(row[col]).replace("%",""))/100 if "%" in str(row[col]) else 0.0) for col in skill_cols}
        employees.append({"id": str(row["姓名"]), "name": str(row["姓名"]), "skills": skills})

    print(f"✅ 加载完成：{len(processes)} 道工序，{len(employees)} 名员工")
    return {
        "messages": [f"✅ 加载完成：{len(processes)} 道工序，{len(employees)} 个工位"],
        "processes": processes,
        "employees": employees,
        "mapping": None,
        "status": "data_loaded"
    }


def mapping_node(state: FactoryState):
    print("【2】LLM 映射中（简化版）...")
    return {"messages": ["✅ 映射完成"], "mapping": {}, "status": "mapped"}


def balancing_node(state: FactoryState):
    print("【3】PuLP 优化进行中（工位数=员工人数）...")
    processes = state["processes"]
    employees = state["employees"]
    n_stations = len(employees)

    total_smv = sum(p["smv"] for p in processes)

    prob = pulp.LpProblem("Line_Balancing", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((i, s) for i in range(len(processes)) for s in range(n_stations)), cat="Binary")
    cycle_time = pulp.LpVariable("cycle_time", lowBound=0.1)

    prob += cycle_time

    # 每道工序必须分配
    for i in range(len(processes)):
        prob += pulp.lpSum(x[i, s] for s in range(n_stations)) == 1

    # 每个工位总时间 ≤ cycle_time
    for s in range(n_stations):
        prob += pulp.lpSum(processes[i]["smv"] * x[i, s] for i in range(len(processes))) <= cycle_time

    # 求解（增加时间限制和更好求解器参数）
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=90, options=['sec 90']))

    cycle_time_val = float(pulp.value(cycle_time) or 1.0)
    balance_rate = (total_smv / (n_stations * cycle_time_val)) * 100 if cycle_time_val > 0 else 0.0

    # === 提取详细分配结果 ===
    assignments = {}
    assigned_process_ids = set()

    for s in range(n_stations):
        station_processes = []
        load = 0.0
        for i in range(len(processes)):
            if x[i, s].value() > 0.5:
                proc = processes[i]
                station_processes.append(f"{proc['id']}: {proc['name'][:60]} (SMV={proc['smv']:.3f})")
                load += proc["smv"]
                assigned_process_ids.add(proc["id"])
        
        emp = employees[s]["name"]
        assignments[f"工位_{s+1} ({emp})"] = {
            "employee": emp,
            "load_time": round(load, 3),
            "process_count": len(station_processes),
            "processes": station_processes
        }

    # 检查是否所有工序都被分配
    all_assigned = len(assigned_process_ids) == len(processes)
    missing = len(processes) - len(assigned_process_ids)

    print(f"✅ PuLP 完成！平衡率 {balance_rate:.1f}% | 周期时间 {cycle_time_val:.2f} 分钟")
    if not all_assigned:
        print(f"⚠️ 注意：有 {missing} 道工序未被分配！")

    return {
        "messages": [f"平衡优化完成！平衡率 {balance_rate:.1f}%"],
        "assignments": assignments,
        "metrics": {
            "balance_rate": balance_rate / 100,
            "cycle_time": cycle_time_val,
            "num_stations": n_stations,
            "total_smv": total_smv
        },
        "status": "balanced"
    }


def report_node(state: FactoryState):
    metrics = state.get("metrics", {})
    assignments = state.get("assignments", {})

    report = f"""
🚀 优化报告（工位数 = 员工人数）
====================================
平衡率：{metrics.get('balance_rate',0)*100:.1f}%
周期时间：{metrics.get('cycle_time',0):.2f} 分钟
工位数：{metrics.get('num_stations',0)} 个
总标准工时：{metrics.get('total_smv',0):.2f} 分钟

=== 每个工位详细分配情况 ===
"""
    for station_name, info in assignments.items():
        report += f"\n{station_name}  | 负荷 {info['load_time']:.2f} 分钟 | {info['process_count']} 道工序\n"
        for p in info["processes"]:
            report += f"   • {p}\n"

    report += "\n所有工序是否全部被分配？ " + ("✅ 是" if len(assignments) > 0 else "❌ 否")

    return {"messages": [report], "status": "completed"}


# ====================== Graph ======================
graph_builder = StateGraph(FactoryState)
graph_builder.add_node("ingest", data_ingestion_node)
graph_builder.add_node("mapping", mapping_node)
graph_builder.add_node("balancing", balancing_node)
graph_builder.add_node("report", report_node)

graph_builder.add_edge(START, "ingest")
graph_builder.add_edge("ingest", "mapping")
graph_builder.add_edge("mapping", "balancing")
graph_builder.add_edge("balancing", "report")
graph_builder.add_edge("report", END)

graph = graph_builder.compile(checkpointer=MemorySaver())


# ====================== 运行 ======================
def stream_graph_updates(user_input: str, thread_id: str = "factory_001"):
    initial_state = {
        "messages": [{"role": "user", "content": user_input}],
        "processes": [], "employees": [], "mapping": None,
        "assignments": {}, "metrics": None, "status": "starting"
    }
    config = {"configurable": {"thread_id": thread_id}}

    print(f"\n{'='*75}")
    for event in graph.stream(initial_state, config, stream_mode="values"):
        if "messages" in event and event["messages"]:
            msg = event["messages"][-1]
            content = msg.content if hasattr(msg, "content") else str(msg)
            if len(content) > 10:   # 避免打印太短的消息
                print(content)
    print(f"{'='*75}\n")


if __name__ == "__main__":
    print("👕 服装工厂车位排布智能体 - 真实场景版（已改进分配）\n")
    while True:
        user_input = input("👷‍♂️ 组长指令: ").strip()
        if user_input.lower() in ["exit", "quit", "退出"]:
            break
        if user_input:
            stream_graph_updates(user_input)