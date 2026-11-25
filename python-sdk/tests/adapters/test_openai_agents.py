"""OpenAI Agents SDK adapter, offline: spans are produced by the SDK itself with a scripted model."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("agents")

from agents import Agent, Runner, function_tool, handoff  # noqa: E402
from agents.items import ModelResponse  # noqa: E402
from agents.models.interface import Model  # noqa: E402
from agents.tracing import custom_span, generation_span, trace  # noqa: E402
from agents.usage import Usage  # noqa: E402
from openai.types.responses import (  # noqa: E402
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

import tervic  # noqa: E402
from tervic.analyze.model import build_view  # noqa: E402
from tervic.api import store_for  # noqa: E402

tervic.attach_openai_agents(replace_default=True)


_IDS = iter(range(1, 100_000))


class Scripted(Model):
    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = turns
        self.turn = 0
        self.i = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        turn = self.turns[min(self.turn, len(self.turns) - 1)]
        self.turn += 1
        self.i = next(_IDS)
        with generation_span(input=[{"role": "user", "content": "x"}], model="gpt-4.1-mini") as span:
            if "tool" in turn:
                output: list[Any] = [ResponseFunctionToolCall(arguments=json.dumps(turn.get("args", {})),
                                                              call_id=f"c{self.i}", name=turn["tool"],
                                                              type="function_call", id=f"f{self.i}")]
                span.span_data.output = [{"role": "assistant", "tool_calls": [
                    {"function": {"name": turn["tool"], "arguments": json.dumps(turn.get("args", {}))}}]}]
            else:
                output = [ResponseOutputMessage(id=f"m{self.i}", role="assistant", status="completed",
                                                type="message", content=[ResponseOutputText(
                                                    text=turn["text"], type="output_text", annotations=[])])]
                span.span_data.output = [{"role": "assistant", "content": turn["text"],
                                          "finish_reason": turn.get("finish", "stop")}]
            span.span_data.usage = {"input_tokens": 10, "output_tokens": 5}
        return ModelResponse(output=output, usage=Usage(requests=1, input_tokens=10, output_tokens=5,
                                                         total_tokens=15), response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _view() -> Any:
    tervic.flush()
    store = store_for()
    return build_view(store.load_events(store.resolve("last")))


def test_tool_and_handoff_spans_become_steps() -> None:
    @function_tool
    def get_balance(account: str) -> str:
        """Balance."""
        return "120 EUR"

    billing = Agent(name="billing", instructions="b", tools=[get_balance],
                    model=Scripted([{"tool": "get_balance", "args": {"account": "a1"}}, {"text": "You have 120 EUR."}]))
    triage = Agent(name="triage", instructions="t", handoffs=[handoff(billing)],
                   model=Scripted([{"tool": "transfer_to_billing"}]))
    result = asyncio.run(Runner.run(triage, "What's my balance?"))
    assert result.final_output == "You have 120 EUR."
    view = _view()
    kinds = {(s.name, s.kind) for s in view.steps}
    assert ("triage", "agent") in kinds and ("billing", "agent") in kinds and ("get_balance", "tool") in kinds
    assert any(kind == "handoff" for _, kind in kinds)
    assert view.tool_calls[0].result == "120 EUR" and view.tool_calls[0].available_tools == ["get_balance"]
    assert len(view.llm_calls) == 3 and view.llm_calls[-1].output == "You have 120 EUR."
    assert view.effective_final_output == "You have 120 EUR."
    assert tervic.check().verdict == "pass"


def test_truncated_generation_is_detected() -> None:
    agent = Agent(name="writer", instructions="w", model=Scripted([{"text": "Once upon a", "finish": "length"}]))
    asyncio.run(Runner.run(agent, "story"))
    assert "TV201" in {f.detector_id for f in tervic.check().findings}


def test_trace_joins_active_tervic_run_and_custom_spans() -> None:
    with tervic.run("outer") as run, trace("inner-trace"), custom_span("guard-check"):
        pass
    tervic.flush()
    view = build_view(store_for().load_events(run.run_id))
    assert [s.name for s in view.steps] == ["guard-check"]
    assert len(store_for().list_runs()) == 1


def test_attach_is_idempotent() -> None:
    assert tervic.attach_openai_agents() is tervic.attach_openai_agents()
