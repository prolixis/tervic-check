"""LangGraph adapter, offline: fake chat model, real LangChain tools and retriever callbacks."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

import pytest

langgraph = pytest.importorskip("langgraph")

from langchain_core.callbacks import CallbackManagerForRetrieverRun  # noqa: E402
from langchain_core.documents import Document  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402
from langchain_core.retrievers import BaseRetriever  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command  # noqa: E402

import tervic  # noqa: E402
from tervic.analyze.model import build_view  # noqa: E402
from tervic.api import store_for  # noqa: E402


class State(TypedDict, total=False):
    question: str
    documents: list[str]
    answer: str
    weather: str


class EmptyRetriever(BaseRetriever):
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return []


@tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return '{"error": "weather service down"}'


def build(llm: Any) -> Any:
    retriever = EmptyRetriever()

    def lookup(state: State) -> dict[str, Any]:
        docs = retriever.invoke(state["question"])
        return {"documents": [d.page_content for d in docs]}

    def weather(state: State) -> Command[Any]:
        return Command(update={"weather": get_weather.invoke({"city": "Paris"})}, goto="answer")

    def answer(state: State) -> dict[str, Any]:
        return {"answer": llm.invoke(f"Q: {state['question']} W: {state['weather']}").content}

    graph = StateGraph(State)
    graph.add_node("lookup", lookup)
    graph.add_node("weather", weather)
    graph.add_node("answer", answer)
    graph.add_edge(START, "lookup")
    graph.add_edge("lookup", "weather")
    graph.add_edge("answer", END)
    return graph.compile()


def _view(run_id: str | None = None) -> Any:
    tervic.flush()
    store = store_for()
    return build_view(store.load_events(store.resolve(run_id)))


def test_invoke_records_nodes_llm_tool_and_retriever() -> None:
    app = build(FakeListChatModel(responses=["Sunny!"]))
    assert tervic.attach(app, kinds={"lookup": "node"}) is app
    assert tervic.attach(app) is app  # idempotent
    result = app.invoke({"question": "weather?"})
    assert result["answer"] == "Sunny!"
    view = _view()
    names = [s.name for s in view.steps]
    assert names[:2] == ["lookup", "EmptyRetriever"] and "weather" in names and "answer" in names
    retriever = next(s for s in view.steps if s.kind == "retriever")
    assert retriever.parent_step_id == view.steps[0].step_id and retriever.output == []
    weather = next(s for s in view.steps if s.name == "weather")
    assert weather.output == {"weather": '{"error": "weather service down"}'}  # Command.update recorded
    assert view.tool_calls[0].tool_name == "get_weather"
    assert view.llm_calls[0].output == "Sunny!" and len(view.llm_calls) == 1
    report = tervic.check()
    assert {"TV301", "TV102"} <= {f.detector_id for f in report.findings}


def test_stream_and_async_entry_points() -> None:
    app = tervic.attach(build(FakeListChatModel(responses=["a", "b", "c"])), name="graph")
    chunks = list(app.stream({"question": "q"}))
    assert chunks and _view().name == "graph"

    async def go() -> None:
        await app.ainvoke({"question": "q"})
        async for _ in app.astream({"question": "q"}):
            pass

    asyncio.run(go())
    assert len(store_for().list_runs()) == 3


def test_joins_an_outer_run_and_records_crash() -> None:
    app = tervic.attach(build(FakeListChatModel(responses=["x"])))
    with tervic.run("outer") as run:
        app.invoke({"question": "q"})
        app.invoke({"question": "q2"})
    assert len(store_for().list_runs()) == 1
    assert sum(1 for s in _view(run.run_id).steps if s.name == "answer") == 2

    def broken(state: State) -> dict[str, Any]:
        raise ValueError("node exploded")

    graph = StateGraph(State)
    graph.add_node("broken", broken)
    graph.add_edge(START, "broken")
    graph.add_edge("broken", END)
    crashing = tervic.attach(graph.compile())
    with pytest.raises(ValueError, match="node exploded"):
        crashing.invoke({"question": "q"})
    report = tervic.check()
    assert report.status == "crashed" and report.verdict == "fail"


def test_non_graph_objects_are_left_alone() -> None:
    from tervic.core.safety import internal_error_counts

    obj = object()
    assert tervic.attach(obj) is obj
    assert "langgraph.attach" in internal_error_counts()
