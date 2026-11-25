"""Event models. Every event is one JSON line in ``events.jsonl``."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

StepKind = Literal["node", "tool", "llm", "retriever", "agent", "handoff", "custom"]
STEP_KINDS: tuple[str, ...] = ("node", "tool", "llm", "retriever", "agent", "handoff", "custom")


def new_ulid() -> str:
    """Return a ULID: 48-bit ms timestamp + 80 random bits, Crockford base32 (26 chars)."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class _Event(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    ts: float = Field(default_factory=time.time)
    seq: int = 0


class GitInfo(BaseModel):
    sha: str | None = None
    branch: str | None = None
    dirty: bool | None = None


class ErrorInfo(BaseModel):
    type: str
    message: str
    traceback: str | None = None


class RunStarted(_Event):
    type: Literal["run_started"] = "run_started"
    name: str
    framework: str
    tags: dict[str, str] = Field(default_factory=dict)
    git: GitInfo = Field(default_factory=GitInfo)
    tervic_version: str
    python_version: str
    started_at: str = Field(default_factory=now_iso)
    input_snapshot: Any = None
    entry_input: Any = None


class StepStarted(_Event):
    type: Literal["step_started"] = "step_started"
    step_id: str
    parent_step_id: str | None = None
    name: str
    kind: StepKind = "custom"
    attempt: int = 1
    input_snapshot: Any = None
    started_at: str = Field(default_factory=now_iso)
    source: str | None = None
    reads: list[str] | None = None


class StepEnded(_Event):
    type: Literal["step_ended"] = "step_ended"
    step_id: str
    output_snapshot: Any = None
    state_diff: dict[str, list[str]] | None = None
    error: ErrorInfo | None = None
    duration_ms: float = 0.0
    ended_at: str = Field(default_factory=now_iso)
    state_mode: Literal["replace", "update"] | None = None


class ToolCallRequest(BaseModel):
    name: str
    args: Any = None


class LLMCall(_Event):
    type: Literal["llm_call"] = "llm_call"
    step_id: str | None = None
    provider: str = "unknown"
    model: str | None = None
    input_messages: Any = None
    output: Any = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    available_tools: list[str] | None = None
    refusal: str | None = None


class ToolCall(_Event):
    type: Literal["tool_call"] = "tool_call"
    step_id: str | None = None
    tool_name: str
    args: Any = None
    result: Any = None
    error: ErrorInfo | None = None
    available_tools: list[str] | None = None
    tool_schema: Any = None


class RunEnded(_Event):
    type: Literal["run_ended"] = "run_ended"
    status: Literal["completed", "crashed"]
    final_output_snapshot: Any = None
    output_set: bool = False
    duration_ms: float = 0.0
    total_cost_usd: float = 0.0
    error: ErrorInfo | None = None
    dropped_events: int = 0


Event = Annotated[
    RunStarted | StepStarted | StepEnded | LLMCall | ToolCall | RunEnded,
    Field(discriminator="type"),
]

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(line: str | bytes) -> Event:
    return _EVENT_ADAPTER.validate_json(line)


def event_to_json(event: _Event) -> str:
    return event.model_dump_json(exclude_none=False)


def to_dict(event: _Event) -> dict[str, Any]:
    return event.model_dump(mode="json")
