"""A one-agent CrewAI research crew shared by broken.py and fixed.py.

The LLM replays a recorded script from ``cassette.json`` so the example runs offline. Tervic is
attached with ``tervic.attach_crewai()``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")

from crewai import Agent, Crew, Task  # noqa: E402
from crewai.llms.base_llm import BaseLLM  # noqa: E402
from crewai.tools import BaseTool  # noqa: E402

import tervic  # noqa: E402

HERE = Path(__file__).parent
tervic.attach_crewai()


class WebSearch(BaseTool):
    name: str = "web_search"
    description: str = "Search the web for a query and return the top result."

    def _run(self, query: str) -> str:
        return "Tervic: open-source SDK that finds silent failures in AI agents."


class RecordedLLM(BaseLLM):
    """Replays a recorded sequence of model replies (offline stand-in for a real LLM)."""

    def __init__(self, script: str) -> None:
        super().__init__(model="recorded-llm")
        self._replies = json.loads((HERE / "cassette.json").read_text(encoding="utf-8"))[script]
        self._index = 0

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None,
             from_task: Any = None, from_agent: Any = None, response_model: Any = None) -> str:
        reply = self._replies[min(self._index, len(self._replies) - 1)]
        self._index += 1
        return str(reply)

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 8000


def run_crew(script: str, topic: str = "tervic") -> str:
    researcher = Agent(role="researcher", goal="Summarize what a product does",
                       backstory="You research software products.", tools=[WebSearch()],
                       llm=RecordedLLM(script), max_iter=8, verbose=False, cache=False)
    task = Task(description=f"Research {topic} and summarize it in one sentence.",
                expected_output="One sentence.", agent=researcher, name="research")
    crew = Crew(agents=[researcher], tasks=[task], verbose=False, name="crewai-research")
    return str(crew.kickoff())
