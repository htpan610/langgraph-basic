from typing import Annotated #给langgraph用的，在类型上附加额外信息
from typing_extensions import TypedDict # 定义固定字典类型
from dotenv import load_dotenv #读取.env的环境变量

load_dotenv()

from langgraph.graph import StateGraph,START # 导入状态图和起始节点
from langgraph.graph.message import add_messages #追加消息，不是覆盖

class State(TypedDict):
    """数据核心结构，定义状态结构（typedict）"""
    messages: Annotated[list[dict], add_messages]

# 定义状态图，告诉langgraph状态结构是State
graph_builder = StateGraph(State)

#初始化大模型
from langchain.chat_models import init_chat_model

llm = init_chat_model("deepseek-chat", model_provider="deepseek")
llm.invoke("你好")#测试大模型是否正常

def chatbot(state:State):
    """定义节点函数，把整个对话历史传进去"""
    return {"messages":[llm.invoke(state["messages"])]}

graph_builder.add_node("chatbot",chatbot)

graph_builder.add_edge(START,"chatbot")

graph=graph_builder.compile() #把定义好的图结构变成可执行图

def stream_graph_updates(user_input:str):
    for event in graph.stream({"messages": [{"role": "user", "content": user_input}]}):
        for value in event.values():
            print("Assistant:",value["messages"][-1].content)
from IPython.display import Image, display

try:
    display(Image(graph.get_graph().draw_mermaid_png()))
except Exception:
    # This requires some extra dependencies and is optional
    pass
while True:
    try:
        user_input=input("User:")
        if user_input.lower()=="exit":
            break
        stream_graph_updates(user_input)
    except:
        user_input="你想知道什么？"
        print(user_input)
        stream_graph_updates(user_input)
        break
