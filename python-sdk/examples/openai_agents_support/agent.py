"""A support agent built with the OpenAI Agents SDK, shared by broken.py and fixed.py.

The model replays recorded responses from ``cassette.json`` (a tool call, then a final answer), so the
example runs offline. Tervic is attached with ``tervic.attach_openai_agents()``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agents import Agent, Runner, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.tracing import generation_span
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

import tervic

HERE = Path(__file__).parent
tervic.attach_openai_agents(replace_default=True)  # keep traces local


class RecordedModel(Model):
    """Replays recorded model turns (offline stand-in for an OpenAI model)."""

    def __init__(self) -> None:
        self.turns = json.loads((HERE / "cassette.json").read_text(encoding="utf-8"))["turns"]
        self.index = 0

    async def get_response(self, system_instructions: Any, input: Any, model_settings: Any, tools: Any,
                           output_schema: Any, handoffs: Any, tracing: Any, previous_response_id: Any = None,
                           conversation_id: Any = None, prompt: Any = None, **kwargs: Any) -> ModelResponse:
        turn = self.turns[min(self.index, len(self.turns) - 1)]
        self.index += 1
        messages = input if isinstance(input, list) else [{"role": "user", "content": str(input)}]
        with generation_span(input=[dict(m) if isinstance(m, dict) else {"content": str(m)} for m in messages],
                             model=turn["model"]) as span:
            if "tool" in turn:
                output: list[Any] = [ResponseFunctionToolCall(
                    arguments=json.dumps(turn["args"]), call_id=f"call_{self.index}", name=turn["tool"],
                    type="function_call", id=f"fc_{self.index}", status="completed",
                )]
                span.span_data.output = [{"role": "assistant", "tool_calls": [
                    {"function": {"name": turn["tool"], "arguments": json.dumps(turn["args"])}}]}]
            else:
                output = [ResponseOutputMessage(
                    id=f"msg_{self.index}", role="assistant", status="completed", type="message",
                    content=[ResponseOutputText(text=turn["text"], type="output_text", annotations=[])],
                )]
                span.span_data.output = [{"role": "assistant", "content": turn["text"],
                                          "finish_reason": "stop"}]
            span.span_data.usage = {"input_tokens": turn["input_tokens"], "output_tokens": turn["output_tokens"]}
        usage = Usage(requests=1, input_tokens=turn["input_tokens"], output_tokens=turn["output_tokens"],
                      total_tokens=turn["input_tokens"] + turn["output_tokens"])
        return ModelResponse(output=output, usage=usage, response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("streaming is not used in this example")


def make_agent(lookup_order: Any) -> Agent[Any]:
    return Agent(name="support-agent", instructions="Answer questions about orders.",
                 tools=[function_tool(lookup_order)], model=RecordedModel())


def run_agent(agent: Agent[Any], question: str) -> str:
    result = asyncio.run(Runner.run(agent, question))
    return str(result.final_output)
