"""A read-only view of a recorded run, rebuilt from its events, used by every analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tervic.core.events import (
    ErrorInfo,
    Event,
    LLMCall,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
    ToolCall,
)


@dataclass
class StepView:
    step_id: str
    name: str
    kind: str
    index: int
    parent_step_id: str | None = None
    attempt: int = 1
    input: Any = None
    output: Any = None
    state_diff: dict[str, list[str]] | None = None
    state_mode: str | None = None
    error: ErrorInfo | None = None
    duration_ms: float = 0.0
    started_ts: float = 0.0
    ended_ts: float | None = None
    source: str | None = None
    reads: list[str] = field(default_factory=list)
    ended: bool = False
    llm_calls: list[LLMCall] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    occurrence: int = 1

    @property
    def state_before(self) -> dict[str, Any] | None:
        return self.input if isinstance(self.input, dict) else None

    @property
    def state_after(self) -> dict[str, Any] | None:
        """Full state after the step (graph-style steps only)."""
        if self.state_mode == "replace":
            return self.output if isinstance(self.output, dict) else None
        if self.state_mode == "update":
            before = self.state_before or {}
            update = self.output if isinstance(self.output, dict) else {}
            return {**before, **update}
        return None

    @property
    def writes(self) -> dict[str, Any]:
        """Fields this step wrote, with their values."""
        if self.state_mode == "update" and isinstance(self.output, dict):
            return dict(self.output)
        if self.state_mode == "replace" and isinstance(self.output, dict):
            before = self.state_before or {}
            return {k: v for k, v in self.output.items() if k not in before or before[k] != v}
        return {}

    @property
    def label(self) -> str:
        return self.name if self.occurrence == 1 else f"{self.name}#{self.occurrence}"


@dataclass
class RunView:
    run_id: str
    name: str = ""
    framework: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    git_sha: str | None = None
    started_at: str | None = None
    started_ts: float = 0.0
    status: str | None = None
    input: Any = None
    final_output: Any = None
    final_output_explicit: bool = False
    duration_ms: float = 0.0
    total_cost: float = 0.0
    error: ErrorInfo | None = None
    dropped_events: int = 0
    ended: bool = False
    steps: list[StepView] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    _by_id: dict[str, StepView] = field(default_factory=dict)

    def step(self, step_id: str | None) -> StepView | None:
        return self._by_id.get(step_id) if step_id else None

    @property
    def root_steps(self) -> list[StepView]:
        return [s for s in self.steps if s.parent_step_id is None or s.parent_step_id not in self._by_id]

    def steps_after(self, step: StepView) -> list[StepView]:
        return [s for s in self.steps if s.index > step.index]

    def steps_before(self, step: StepView) -> list[StepView]:
        return [s for s in self.steps if s.index < step.index]

    @property
    def tokens(self) -> int:
        return sum((c.input_tokens or 0) + (c.output_tokens or 0) for c in self.llm_calls)

    @property
    def effective_final_output(self) -> Any:
        if self.final_output_explicit:
            return self.final_output
        roots = [s for s in self.root_steps if s.ended and s.error is None]
        if roots:
            last = roots[-1]
            after = last.state_after
            return after if after is not None else last.output
        return self.final_output

    def owner_step(self, call: LLMCall | ToolCall) -> StepView | None:
        return self.step(call.step_id)


def build_view(events: list[Event]) -> RunView:
    ordered = sorted(events, key=lambda e: (e.ts, e.seq))
    view = RunView(run_id=ordered[0].run_id if ordered else "")
    occurrences: dict[str, int] = {}
    for event in ordered:
        if isinstance(event, RunStarted):
            view.name = event.name
            view.framework = event.framework
            view.tags = dict(event.tags)
            view.git_sha = event.git.sha
            view.started_at = event.started_at
            view.started_ts = event.ts
            view.input = event.input_snapshot
        elif isinstance(event, StepStarted):
            occurrences[event.name] = occurrences.get(event.name, 0) + 1
            reads = event.reads
            step = StepView(
                step_id=event.step_id,
                name=event.name,
                kind=event.kind,
                index=len(view.steps),
                parent_step_id=event.parent_step_id,
                attempt=event.attempt,
                input=event.input_snapshot,
                started_ts=event.ts,
                source=event.source,
                reads=list(reads) if isinstance(reads, list) else [],
                occurrence=occurrences[event.name],
            )
            view.steps.append(step)
            view._by_id[step.step_id] = step
        elif isinstance(event, StepEnded):
            ended = view._by_id.get(event.step_id)
            if ended is None:
                continue
            ended.output = event.output_snapshot
            ended.state_diff = event.state_diff
            ended.state_mode = event.state_mode
            ended.error = event.error
            ended.duration_ms = event.duration_ms
            ended.ended_ts = event.ts
            ended.ended = True
        elif isinstance(event, LLMCall):
            view.llm_calls.append(event)
            owner = view._by_id.get(event.step_id or "")
            if owner is not None:
                owner.llm_calls.append(event)
        elif isinstance(event, ToolCall):
            view.tool_calls.append(event)
            owner = view._by_id.get(event.step_id or "")
            if owner is not None:
                owner.tool_calls.append(event)
        elif isinstance(event, RunEnded):
            view.ended = True
            view.status = event.status
            view.final_output = event.final_output_snapshot
            view.final_output_explicit = event.output_set
            view.duration_ms = event.duration_ms
            view.total_cost = event.total_cost_usd
            view.error = event.error
            view.dropped_events = event.dropped_events
    if not view.total_cost:
        view.total_cost = sum(c.cost_usd or 0.0 for c in view.llm_calls)
    return view
