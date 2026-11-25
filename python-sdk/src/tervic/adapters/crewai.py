"""CrewAI: ``tervic.attach_crewai()`` (``pip install tervic[crewai]``).

Registers a CrewAI event listener (``crewai.events.BaseEventListener``). Each crew kickoff becomes a
run; tasks become ``agent`` steps, tool executions ``tool`` steps, and every agent action (the tool
the agent asked for, its input and the observation it got back) a tool call, so loops are visible
even when CrewAI refuses to re-run an identical tool call.

CrewAI runs synchronous handlers in a thread pool: they do not see the caller's context variables
and do not run in order (verified in crewai 1.6.1). The handlers therefore only enqueue events; a
small Tervic thread processes them in CrewAI's own timestamp order once they are 0.3 s old, and
``tervic.flush()`` / ``tervic.check()`` drain everything immediately.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from tervic.core.recorder import add_flush_hook
from tervic.core.run import Run
from tervic.core.safety import report_internal_error

SETTLE_S = 0.3
QUIET_S = 0.2
_listener: Any = None
_lock = threading.Lock()


def _ts(event: Any) -> float:
    stamp = getattr(event, "timestamp", None)
    if isinstance(stamp, datetime):
        return stamp.timestamp()
    return time.time()


def _text(value: Any) -> Any:
    raw = getattr(value, "raw", None)
    return raw if raw is not None else value


def _id(obj: Any) -> str:
    value = getattr(obj, "id", None)
    return str(value) if value is not None else ""


class _CrewState:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.tasks: dict[str, str] = {}
        self.agent_tools: dict[str, list[str]] = {}
        self.tool_steps: dict[tuple[str, str], list[str]] = {}
        self.llm: dict[str, dict[str, Any]] = {}
        self.agent_task: dict[str, str] = {}
        self.crew_name: str | None = None
        self.running: list[str] = []
        self.task_order: list[tuple[str, str, str | None]] = []
        self.ended = False


class _Processor:
    """Turns ordered CrewAI events into Tervic run events."""

    def __init__(self) -> None:
        self.crews: dict[str, _CrewState] = {}
        self.by_task: dict[str, _CrewState] = {}
        self.by_agent: dict[str, _CrewState] = {}

    def state(self, event: Any) -> _CrewState | None:
        task_id = self.task_key(event)
        if task_id and task_id in self.by_task:
            return self.by_task[task_id]
        agent_id = str(
            getattr(event, "agent_id", None)
            or _id(getattr(event, "from_agent", None))
            or _id(getattr(event, "agent", None))
        )
        if agent_id and agent_id in self.by_agent:
            return self.by_agent[agent_id]
        live = [s for s in self.crews.values() if not s.ended]
        return live[-1] if live else None

    def task_step(self, state: _CrewState, event: Any, ts: float | None = None) -> str | None:
        """The step of the task this event belongs to, creating it on first sight.

        CrewAI 1.6 delivers TaskStarted/TaskCompleted through its dependency-ordered async path, so they
        can arrive after the task's tool events (or not before the crew finishes). Task steps are
        therefore created from whichever event mentions the task first.
        """
        key = self.task_key(event)
        if key and key in state.tasks:
            return state.tasks[key]
        if state.running:
            if key:
                state.tasks[key] = state.running[-1]
            return state.running[-1]
        pending = [t for t in state.task_order if t[0] not in state.tasks]
        if not pending:
            return None
        task_id, name, role = pending[0]
        stamp = (ts if ts is not None else _ts(event)) - 1e-6
        step_id = state.run.step_start(name, "agent", {"agent": role}, parent_step_id=None, ts=stamp)
        state.tasks[task_id] = step_id
        if key:
            state.tasks[key] = step_id
        state.running.append(step_id)
        return step_id

    @staticmethod
    def task_key(event: Any) -> str:
        return str(
            getattr(event, "task_id", None)
            or _id(getattr(event, "task", None))
            or _id(getattr(event, "from_task", None))
            or ""
        )

    # ---------------------------------------------------------------- per event type
    def kickoff_started(self, source: Any, event: Any) -> None:
        crew = event.crew or source
        run = Run(
            event.crew_name or getattr(crew, "name", None) or "crew",
            framework="crewai",
            input=event.inputs or {},
            ts=_ts(event),
        )
        state = _CrewState(run)
        state.crew_name = event.crew_name or getattr(crew, "name", None)
        self.crews[_id(crew) or str(id(crew))] = state
        for task in getattr(crew, "tasks", None) or []:
            self.by_task[_id(task)] = state
            name = getattr(task, "name", None) or (getattr(task, "description", None) or "task")[:40]
            role = getattr(getattr(task, "agent", None), "role", None)
            state.task_order.append((_id(task), str(name), role))
        for agent in getattr(crew, "agents", None) or []:
            self.by_agent[_id(agent)] = state
            state.agent_tools[_id(agent)] = [t.name for t in (getattr(agent, "tools", None) or [])]

    def _crew_for_finish(self, source: Any, event: Any) -> _CrewState | None:
        """CrewAI may report completion from a copy of the crew, so fall back to name / sole live crew."""
        crew = event.crew or source
        state = self.crews.get(_id(crew) or str(id(crew)))
        if state is not None and not state.ended:
            return state
        live = [s for s in self.crews.values() if not s.ended]
        name = getattr(event, "crew_name", None) or getattr(crew, "name", None)
        named = [s for s in live if name and s.crew_name == name]
        if named:
            return named[-1]
        return live[-1] if len(live) == 1 else None

    def kickoff_finished(self, source: Any, event: Any, failed: bool) -> None:
        state = self._crew_for_finish(source, event)
        if state is None or state.ended:
            return
        state.ended = True
        for index, step_id in enumerate(list(state.running)):
            last = index == len(state.running) - 1
            output = None if failed or not last else _text(event.output)
            error = ("CrewKickoffFailed", str(event.error)) if failed else None
            state.run.step_end(step_id, output, error=error, ts=_ts(event))
        state.running.clear()
        if failed:
            state.run.end(status="crashed", error=("CrewKickoffFailed", str(event.error)), wait=False)
        else:
            state.run.end(status="completed", final_output=_text(event.output), wait=False)

    def task_started(self, source: Any, event: Any) -> None:
        task = event.task or source
        key = self.task_key(event) or _id(task)
        state = self.by_task.get(key) or self.state(event)
        if state is None or key in state.tasks:
            return  # already created from an earlier event of this task
        name = (
            event.task_name
            or getattr(task, "name", None)
            or (getattr(task, "description", None) or "task")[:40]
        )
        step_id = state.run.step_start(
            str(name),
            "agent",
            {
                "context": event.context,
                "agent": event.agent_role or getattr(getattr(task, "agent", None), "role", None),
            },
            parent_step_id=None,
            ts=_ts(event),
        )
        state.tasks[key] = step_id
        state.running.append(step_id)
        agent = getattr(task, "agent", None)
        if agent is not None:
            state.agent_task[_id(agent)] = step_id

    def task_finished(self, source: Any, event: Any, failed: bool) -> None:
        key = self.task_key(event) or _id(event.task or source)
        state = self.by_task.get(key) or self.state(event)
        step_id = state.tasks.get(key) if state else None
        if state is not None and step_id is None and state.running:
            step_id = state.running[-1]
        if state is None or step_id is None:
            return
        if step_id in state.running:
            state.running.remove(step_id)
        if failed:
            state.run.step_end(step_id, None, error=("TaskFailed", str(event.error)), ts=_ts(event))
        else:
            state.run.step_end(step_id, _text(event.output), ts=_ts(event))

    def tool_started(self, source: Any, event: Any) -> None:
        state = self.state(event)
        if state is None:
            return
        parent = self.task_step(state, event, _ts(event))
        step_id = state.run.step_start(
            event.tool_name, "tool", event.tool_args, parent_step_id=parent, ts=_ts(event)
        )
        state.tool_steps.setdefault((self.task_key(event), event.tool_name), []).append(step_id)

    def tool_finished(self, source: Any, event: Any, error: Any = None) -> None:
        state = self.state(event)
        if state is None:
            return
        pending = state.tool_steps.get((self.task_key(event), event.tool_name)) or []
        step_id = pending.pop(0) if pending else None
        if step_id is None:
            return
        if error is not None:
            state.run.step_end(step_id, None, error=("ToolError", str(error)), ts=_ts(event))
        else:
            state.run.step_end(step_id, getattr(event, "output", None), ts=_ts(event))

    def agent_action(self, source: Any, event: Any) -> None:
        answer = getattr(event, "formatted_answer", None)
        tool = getattr(answer, "tool", None)
        if not tool:
            return
        agent_key = str(event.agent_id or "") or _id(source)
        state = self.by_agent.get(agent_key) or self.state(event)
        if state is None:
            return
        tools = state.agent_tools.get(agent_key) or None
        if tools is None:
            merged = sorted({t for names in state.agent_tools.values() for t in names})
            tools = merged or None
        state.run.tool_call(
            str(tool),
            args=getattr(answer, "tool_input", None),
            result=getattr(answer, "result", None),
            available_tools=tools,
            step_id=state.agent_task.get(agent_key) or self.task_step(state, event, _ts(event)),
            ts=_ts(event),
        )

    def llm_started(self, source: Any, event: Any) -> None:
        state = self.state(event)
        if state is None:
            return
        names = [
            (t.get("function") or {}).get("name") or t.get("name")
            for t in (event.tools or [])
            if isinstance(t, dict)
        ]
        state.llm[self.task_key(event) or str(event.agent_id or "")] = {
            "messages": event.messages,
            "model": event.model,
            "start": _ts(event),
            "tools": [n for n in names if n] or None,
        }

    def llm_finished(self, source: Any, event: Any, failed: bool) -> None:
        state = self.state(event)
        if state is None:
            return
        start = state.llm.pop(self.task_key(event) or str(event.agent_id or ""), None) or {}
        if failed:
            return
        response = event.response
        text = response if isinstance(response, str) else getattr(response, "content", response)
        state.run.llm_call(
            provider="crewai",
            model=event.model or start.get("model"),
            input_messages=event.messages or start.get("messages"),
            output=text,
            latency_ms=(_ts(event) - start["start"]) * 1000 if "start" in start else None,
            available_tools=start.get("tools"),
            step_id=self.task_step(state, event),
            ts=_ts(event),
        )


def _make_listener_class() -> type:
    from crewai import events as ev

    class TervicCrewListener(ev.BaseEventListener):  # type: ignore[misc,unused-ignore]
        def __init__(self) -> None:
            self._lock = threading.Condition()
            self._pending: list[tuple[float, int, Callable[[], None]]] = []
            self._seq = itertools.count()
            self._inflight = 0
            self._last_enqueue = 0.0
            self.processor = _Processor()
            self._process_lock = threading.Lock()
            super().__init__()
            self._thread = threading.Thread(target=self._loop, name="tervic-crewai", daemon=True)
            self._thread.start()

        # ---------------------------------------------------------- queue
        def _enqueue(self, event: Any, action: Callable[[], None]) -> None:
            with self._lock:
                self._pending.append((_ts(event), next(self._seq), action))
                self._last_enqueue = time.time()
                self._lock.notify_all()

        def _drain(self, settle: float) -> None:
            with self._process_lock:
                cutoff = time.time() - settle
                with self._lock:
                    self._pending.sort(key=lambda item: (item[0], item[1]))
                    ready = (
                        [item for item in self._pending if item[0] <= cutoff]
                        if settle
                        else list(self._pending)
                    )
                    self._pending = [item for item in self._pending if item not in ready]
                for _, _, action in ready:
                    try:
                        action()
                    except Exception as exc:
                        report_internal_error("crewai.process", exc)

        def _loop(self) -> None:
            while True:
                time.sleep(0.1)
                self._drain(SETTLE_S)

        def wait_idle(self, timeout: float) -> None:
            """Wait until CrewAI's handler pool is quiet (no handler running, nothing new for 0.2 s)."""
            deadline = time.time() + timeout
            with self._lock:
                while time.time() < deadline and (
                    self._inflight > 0 or (self._pending and time.time() - self._last_enqueue < QUIET_S)
                ):
                    self._lock.wait(0.02)
            self._drain(0.0)

        # ---------------------------------------------------------- registration
        def setup_listeners(self, bus: Any) -> None:
            proc_ = self

            def register(event_type: Any, fn: Callable[[_Processor, Any, Any], None]) -> None:
                def handler(source: Any, event: Any) -> None:
                    with proc_._lock:
                        proc_._inflight += 1
                    try:
                        proc_._enqueue(event, lambda: fn(proc_.processor, source, event))
                    except Exception as exc:
                        report_internal_error("crewai.enqueue", exc)
                    finally:
                        with proc_._lock:
                            proc_._inflight -= 1
                            proc_._lock.notify_all()

                bus.on(event_type)(handler)

            register(ev.CrewKickoffStartedEvent, lambda p, s, e: p.kickoff_started(s, e))
            register(ev.CrewKickoffCompletedEvent, lambda p, s, e: p.kickoff_finished(s, e, failed=False))
            register(ev.CrewKickoffFailedEvent, lambda p, s, e: p.kickoff_finished(s, e, failed=True))
            register(ev.TaskStartedEvent, lambda p, s, e: p.task_started(s, e))
            register(ev.TaskCompletedEvent, lambda p, s, e: p.task_finished(s, e, failed=False))
            register(ev.TaskFailedEvent, lambda p, s, e: p.task_finished(s, e, failed=True))
            register(ev.ToolUsageStartedEvent, lambda p, s, e: p.tool_started(s, e))
            register(ev.ToolUsageFinishedEvent, lambda p, s, e: p.tool_finished(s, e))
            register(ev.ToolUsageErrorEvent, lambda p, s, e: p.tool_finished(s, e, error=e.error))
            register(ev.AgentLogsExecutionEvent, lambda p, s, e: p.agent_action(s, e))
            register(ev.LLMCallStartedEvent, lambda p, s, e: p.llm_started(s, e))
            register(ev.LLMCallCompletedEvent, lambda p, s, e: p.llm_finished(s, e, failed=False))
            register(ev.LLMCallFailedEvent, lambda p, s, e: p.llm_finished(s, e, failed=True))

    return TervicCrewListener


def attach_crewai() -> Any:
    """Register Tervic as a CrewAI event listener. Safe to call more than once."""
    global _listener
    with _lock:
        if _listener is not None:
            return _listener
        try:
            _listener = _make_listener_class()()
            add_flush_hook(_listener.wait_idle)
        except Exception as exc:
            report_internal_error("crewai.attach", exc)
        return _listener
