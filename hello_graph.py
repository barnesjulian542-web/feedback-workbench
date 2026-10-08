"""第一个实验：不需要 API Key，不调用模型。"""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END


class State(TypedDict):
    text: str
    result: str


def analyze(state):
    return {"result": "收到反馈：" + state["text"]}


builder = StateGraph(State)
builder.add_node("analyze", analyze)
builder.add_edge(START, "analyze")
builder.add_edge("analyze", END)
graph = builder.compile()

if __name__ == "__main__":
    print(graph.invoke({"text": "希望支持导出 Excel", "result": ""}))
