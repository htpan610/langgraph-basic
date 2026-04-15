from typing import Annotated
from typing_extensions import TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langchain.chat_models import init_chat_model
from IPython.display import Image, display

# 读取 .env 环境变量
load_dotenv()


# 定义状态结构
class State(TypedDict):
    messages: Annotated[list[dict], add_messages]


# 初始化模型
llm = init_chat_model("deepseek-chat", model_provider="deepseek")
llm.invoke("你好")  # 测试模型


# 定义节点函数
def chatbot(state: State):
    print(state)
    return {"messages": [llm.invoke(state["messages"])]}


# 构建图
graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")
graph = graph_builder.compile()


# 运行图
def stream_graph_updates(user_input: str):
    for event in graph.stream(
        {"messages": [{"role": "user", "content": user_input}]}
    ):
        print(event)
        for value in event.values():
            print("Assistant:", value["messages"][-1].content)

# 聊天循环
while True:
    try:
        user_input = input("User: ")
        if user_input.lower() == "exit":
            break
        stream_graph_updates(user_input)
    except:
        user_input = "你想知道什么？"
        print(user_input)
        stream_graph_updates(user_input)
        break