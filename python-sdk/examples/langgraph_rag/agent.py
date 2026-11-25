"""A small LangGraph RAG agent shared by broken.py and fixed.py.

The LLM is a LangChain chat model that replays recorded answers from ``cassette.json``, so the
example runs offline with no API key. Swap ``make_llm`` for ``ChatOpenAI(...)`` to run it for real.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import END, START, StateGraph

import tervic

HERE = Path(__file__).parent

DOCUMENTS = [
    {"id": "d1", "product": "tervic", "text": "Tervic records every step of an AI agent run."},
    {"id": "d2", "product": "tervic", "text": "Tervic finds the step that silently broke and fails CI."},
    {"id": "d3", "product": "other", "text": "An unrelated product page about coffee machines."},
]


class State(TypedDict, total=False):
    question: str
    product: str
    documents: list[dict[str, Any]]
    answer: str


def make_llm() -> FakeListChatModel:
    responses = json.loads((HERE / "cassette.json").read_text(encoding="utf-8"))["responses"]
    return FakeListChatModel(responses=responses)


def build(retrieve: Callable[[State], dict[str, Any]]) -> Any:
    llm = make_llm()

    def answer(state: State) -> dict[str, Any]:
        context = "\n".join(doc["text"] for doc in state["documents"])
        prompt = f"Answer using only this context.\nContext:\n{context}\n\nQuestion: {state['question']}"
        return {"answer": llm.invoke(prompt).content}

    graph = StateGraph(State)
    graph.add_node("retrieve", retrieve)
    graph.add_node("answer", answer)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", END)
    return tervic.attach(graph.compile(), name="langgraph-rag")
