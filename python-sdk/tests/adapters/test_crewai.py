"""CrewAI adapter, offline: a scripted LLM, real CrewAI agents/tasks/tools and its event bus."""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
pytest.importorskip("crewai")

from crewai import Agent, Crew, Task  # noqa: E402
from crewai.llms.base_llm import BaseLLM  # noqa: E402
from crewai.tools import BaseTool  # noqa: E402

import tervic  # noqa: E402
from tervic.analyze.model import build_view  # noqa: E402
from tervic.api import store_for  # noqa: E402

tervic.attach_crewai()


class Scripted(BaseLLM):
    def __init__(self, replies: list[str]) -> None:
        super().__init__(model="scripted")
        self.replies = replies
        self.i = 0

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None,
             from_task: Any = None, from_agent: Any = None, response_model: Any = None) -> str:
        reply = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return reply

    def supports_function_calling(self) -> bool:
        return False


class Flaky(BaseTool):
    name: str = "flaky_api"
    description: str = "Calls an API."

    def _run(self, query: str) -> str:
        raise RuntimeError("API down")


class Echo(BaseTool):
    name: str = "echo"
    description: str = "Echo."

    def _run(self, query: str) -> str:
        return f"echo {query}"


def _crew(tool: BaseTool, replies: list[str], name: str = "crew") -> Crew:
    agent = Agent(role="worker", goal="g", backstory="b", tools=[tool], llm=Scripted(replies), max_iter=3,
                  verbose=False, cache=False)
    task = Task(description="Do the thing.", expected_output="x", agent=agent, name="do-thing")
    return Crew(agents=[agent], tasks=[task], verbose=False, name=name)


def _view() -> Any:
    tervic.flush()
    store = store_for()
    return build_view(store.load_events(store.resolve("last")))


def test_task_tool_and_final_output_are_recorded() -> None:
    crew = _crew(Echo(), ['Thought: x\nAction: echo\nAction Input: {"query": "hi"}',
                          "Thought: done\nFinal Answer: echo hi"], name="echo-crew")
    assert "echo hi" in str(crew.kickoff())
    view = _view()
    assert view.name == "echo-crew" and view.framework == "crewai"
    task = next(s for s in view.steps if s.kind == "agent")
    assert task.name == "do-thing"
    tools = [s for s in view.steps if s.kind == "tool"]
    assert tools and tools[0].parent_step_id == task.step_id and tools[0].output == "echo hi"
    assert view.tool_calls[0].available_tools == ["echo"]
    assert "echo hi" in str(view.final_output)
    assert tervic.check().verdict == "pass"


def test_tool_error_is_recorded() -> None:
    crew = _crew(Flaky(), ['Thought: x\nAction: flaky_api\nAction Input: {"query": "q"}',
                           "Thought: done\nFinal Answer: all good"])
    crew.kickoff()
    view = _view()
    assert any("API down" in str(c.result) or c.error for c in view.tool_calls)
    assert "TV102" in {f.detector_id for f in tervic.check().findings}


def test_kickoff_failure_marks_run_crashed() -> None:
    class Boom(BaseLLM):
        def __init__(self) -> None:
            super().__init__(model="boom")

        def call(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("model unavailable")

    agent = Agent(role="w", goal="g", backstory="b", llm=Boom(), max_iter=1, verbose=False)
    crew = Crew(agents=[agent], tasks=[Task(description="d", expected_output="x", agent=agent)], verbose=False)
    with pytest.raises(Exception, match="model unavailable"):
        crew.kickoff()
    report = tervic.check()
    assert report.status == "crashed" and report.verdict == "fail"
