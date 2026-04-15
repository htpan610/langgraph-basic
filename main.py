from typing import Annotated
from typing_extensions import TypedDict
from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph,START
from langgraph.graph.message import add_messages

class State(TypedDict):
    messages: Annotated[list[dict], add_messages]

graph_builder = StateGraph(State)

import os
from langchain.chat_models import init_chat_model

llm = init_chat_model("deepseek-chat", model_provider="deepseek")
llm.invoke("你好")

def chatbot(state:State):
    return {"messages":[llm.invoke(state["messages"])]}

graph_builder.add_node("chatbot",chatbot)

graph_builder.add_edge(START,"chatbot")

graph=graph_builder.compile()

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
