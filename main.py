from typing import Annotated, List, Dict, Any
from typing_extensions import TypedDict
from dotenv import load_dotenv

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from langchain.chat_models import init_chat_model

load_dotenv()

# ====================== 1. State 定义 ======================
class Process(TypedDict):
    id: str
    name: str
    smv: float
    predecessors: List[str]
    machine_type: str

class Employee(TypedDict):
    id: str
    name: str
    skills: Dict[str, float]

class Metrics(TypedDict):
    balance_rate: float
    total_distance: float
    efficiency: float
    idle_time: float

class FactoryState(TypedDict):
    messages: Annotated[list[dict], add_messages]
    
    processes: List[Process]
    employees: List[Employee]
    factory_config: Dict[str, Any]
    
    takt_time: float | None
    precedence_graph: Dict | None
    
    assignments: Dict[str, Any]
    station_layout: Dict[str, Any]
    metrics: Metrics | None
    
    iteration_count: int
    status: str


# ====================== 2. LLM ======================
llm = init_chat_model("deepseek-chat", model_provider="deepseek")


# ====================== 3. 节点函数 ======================
def data_ingestion_node(state: FactoryState):
    print("【节点1】数据摄入中...")
    return {
        "messages": ["数据已接收，正在解析工序信息..."],
        "iteration_count": state.get("iteration_count", 0) + 1,
        "status": "data_loaded"
    }

def analysis_node(state: FactoryState):
    print("【节点2】数据分析中...")
    return {
        "messages": ["已完成工序优先关系分析和节拍时间计算。"],
        "status": "analyzed"
    }

def balancing_node(state: FactoryState):
    print("【节点3】生产线平衡优化中...")
    fake_balance = 0.82
    return {
        "messages": [f"平衡优化完成，当前平衡率: {fake_balance:.1%}"],
        "metrics": {"balance_rate": fake_balance, "total_distance": 0.0, "efficiency": 0.0, "idle_time": 0.0},
        "assignments": {"station_1": ["A", "B"], "station_2": ["D", "E"], "station_3": ["F", "G"]},
        "status": "balanced"
    }

def layout_node(state: FactoryState):
    print("【节点4】车位布局优化中...")
    return {
        "messages": ["车位布局优化完成，已尽量缩短物料传递距离。"],
        "station_layout": {"station_1": (0, 0), "station_2": (4, 0), "station_3": (8, 0)},
        "status": "layout_done"
    }

def report_node(state: FactoryState):
    print("【节点5】生成最终报告...")
    metrics = state.get("metrics") or {}
    balance_rate = metrics.get("balance_rate", 0.0)
    return {
        "messages": [f"✅ 优化完成！\n生产线平衡率: {balance_rate:.1%}\n物料传递距离已优化。\n建议：组长可进一步人工微调。"],
        "status": "completed"
    }


# ====================== 4. 构建 Graph ======================
graph_builder = StateGraph(FactoryState)

graph_builder.add_node("ingest", data_ingestion_node)
graph_builder.add_node("analyze", analysis_node)
graph_builder.add_node("balancing", balancing_node)
graph_builder.add_node("layout", layout_node)
graph_builder.add_node("report", report_node)

graph_builder.add_edge(START, "ingest")
graph_builder.add_edge("ingest", "analyze")
graph_builder.add_edge("analyze", "balancing")
graph_builder.add_edge("balancing", "layout")
graph_builder.add_edge("layout", "report")
graph_builder.add_edge("report", END)

checkpointer = MemorySaver()
graph = graph_builder.compile(checkpointer=checkpointer)


# ====================== 5. 运行函数（已彻底修复格式化问题） ======================
def stream_graph_updates(user_input: str, thread_id: str = "factory_test"):
    initial_state: FactoryState = {
        "messages": [{"role": "user", "content": user_input}],
        "processes": [],
        "employees": [],
        "factory_config": {},
        "takt_time": None,
        "precedence_graph": None,
        "assignments": {},
        "station_layout": {},
        "metrics": None,
        "iteration_count": 0,
        "status": "starting"
    }
    
    config = {"configurable": {"thread_id": thread_id}}
    
    print(f"\n=== 开始处理: {thread_id} ===")
    
    for event in graph.stream(initial_state, config, stream_mode="values"):
        # 安全打印消息
        if "messages" in event and event["messages"]:
            last_msg = event["messages"][-1]
            content = getattr(last_msg, "content", str(last_msg))
            print(f"→ {content}")
        
        # 安全打印状态和指标
        status = event.get("status", "unknown")
        metrics = event.get("metrics") or {}
        balance_rate = metrics.get("balance_rate")
        
        if balance_rate is not None:
            print(f"   状态: {status} | 平衡率: {balance_rate:.1%}")
        else:
            print(f"   状态: {status} | 平衡率: —")
    
    print("=== 本次优化完成 ===\n")


# ====================== 6. 主程序 ======================
if __name__ == "__main__":
    print("服装工厂车位排布智能体（LangGraph 阶段1 - 修复版）已启动！")
    print("输入 'exit' 或 'quit' 退出。\n")
    
    while True:
        try:
            user_input = input("User: ")
            if user_input.lower() in ["exit", "quit", "退出"]:
                print("再见！")
                break
            if user_input.strip() == "":
                continue
                
            stream_graph_updates(user_input)
            
        except KeyboardInterrupt:
            print("\n程序已停止")
            break
        except Exception as e:
            print(f"发生错误: {e}")