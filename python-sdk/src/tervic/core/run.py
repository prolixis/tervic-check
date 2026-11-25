"""Run context: one recorded agent run, its steps, and the context variables that track them."""

from __future__ import annotations

import contextvars
import json
import platform
import random
import subprocess
import threading
import time
import traceback as tb
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, cast

from tervic._version import __version__
from tervic.config import TervicConfig, get_config
from tervic.core.events import (
    STEP_KINDS,
    ErrorInfo,
    GitInfo,
    LLMCall,
    RunEnded,
    RunStarted,
    StepEnded,
    StepKind,
    StepStarted,
    ToolCall,
    ToolCallRequest,
    _Event,
    event_to_json,
    new_ulid,
)
from tervic.core.recorder import get_writer, register_active, unregister_active
from tervic.core.redact import Redactor, find_secrets
from tervic.core.safety import guard, report_internal_error
from tervic.core.serialize import MARK, snapshot, state_diff, text_of
from tervic.store.cassette import Cassette, make_entry
from tervic.store.local import LocalStore, RunRow

if TYPE_CHECKING:
    from tervic.analyze.engine import Report
    from tervic.core.events import Event

Verdict = Literal["pass", "warn", "fail"]

_RUN: contextvars.ContextVar[Run | None] = contextvars.ContextVar("tervic_run", default=None)
_STEP: contextvars.ContextVar[str | None] = contextvars.ContextVar("tervic_step", default=None)

_MISSING: Any = object()


def current_run() -> Run | None:
    return _RUN.get()


def current_step_id() -> str | None:
    return _STEP.get()


# ---------------------------------------------------------------- git info (cached per process)

_git_cache: dict[str, GitInfo] = {}
_git_lock = threading.Lock()


def _git(args: list[str], cwd: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_info(root: str = ".") -> GitInfo:
    key = str(Path(root).resolve())
    with _git_lock:
        if key in _git_cache:
            return _git_cache[key]
    sha = _git(["rev-parse", "HEAD"], key)
    info = GitInfo(
        sha=sha,
        branch=_git(["rev-parse", "--abbrev-ref", "HEAD"], key) if sha else None,
        dirty=bool(_git(["status", "--porcelain"], key)) if sha else None,
    )
    with _git_lock:
        _git_cache[key] = info
    return info


# ---------------------------------------------------------------- replay state


class ReplayState:
    """Active during ``tervic replay``: serve recorded values until ``from_step`` is reached."""

    def __init__(self, cassette: Cassette, from_step: str | None, offline: bool = False) -> None:
        self.cassette = cassette
        self.from_step = from_step
        self.offline = offline
        self.reached = from_step is None

    def before_from(self, step_name: str) -> bool:
        if not self.reached and step_name == self.from_step:
            self.reached = True
        return not self.reached

    def serve_llm(self) -> bool:
        return self.offline or not self.reached


_replay: ReplayState | None = None


def replay_state() -> ReplayState | None:
    return _replay


def set_replay_state(state: ReplayState | None) -> None:
    global _replay
    _replay = state


# ---------------------------------------------------------------- the run


class Run:
    """One recorded agent run. Create it with ``tervic.run(...)`` or let an adapter create it."""

    def __init__(
        self,
        name: str,
        *,
        framework: str = "python",
        tags: dict[str, str] | None = None,
        input: Any = _MISSING,
        config: TervicConfig | None = None,
        ts: float | None = None,
    ) -> None:
        self.config = config or get_config()
        self.run_id = new_ulid()
        self.name = name
        self.framework = framework
        self.tags = {str(k): str(v) for k, v in (tags or {}).items()}
        self.active = self.config.enabled
        self.mode = self.config.mode
        self.store = LocalStore(self.config.store_path())
        self.redactor = Redactor(self.config.redact, self.config.redact_pii, self.config.redact_fn)
        record_cassette = self.config.record_cassette
        self.record_cassette = (self.mode != "prod") if record_cassette is None else record_cassette
        self.stream = self.mode != "prod"
        self.events: list[Event] = []
        self.dropped = 0
        self.total_cost = 0.0
        self.started_ts = ts if ts is not None else time.time()
        self.status: str | None = None
        self.report: Report | None = None
        self.kept: bool | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._ended = False
        self._step_inputs: dict[str, Any] = {}
        self._step_names: dict[str, str] = {}
        self._step_started: dict[str, float] = {}
        self._step_ts: dict[str, float] = {}
        self._occurrences: dict[str, int] = {}
        self._last_end: tuple[str, bool] | None = None
        self._attempts: dict[str, int] = {}
        self._final_output: Any = None
        self._output_set = False
        if not self.active:
            self._done.set()
            return
        register_active(self)
        with guard("run.start"):
            entry_input = None
            if input is not _MISSING and self.record_cassette:
                entry = self._exact(input)
                entry_input = entry["value"] if entry.get("replayable") else None
            self.emit(
                RunStarted(
                    run_id=self.run_id,
                    name=name,
                    framework=framework,
                    tags=self.tags,
                    git=git_info(self.config.root),
                    tervic_version=__version__,
                    python_version=platform.python_version(),
                    input_snapshot=self.snap(input) if input is not _MISSING else None,
                    entry_input=entry_input,
                ),
                ts,
            )

    # -------------------------------------------------------------- low level
    def __hash__(self) -> int:
        return hash(self.run_id)

    def snap(self, value: Any) -> Any:
        try:
            return self.redactor(snapshot(value))
        except Exception as exc:
            report_internal_error("run.snapshot", exc)
            return None

    def _exact(self, value: Any) -> dict[str, Any]:
        entry = make_entry("input", "entry", value)
        if entry.get("replayable") and self.config.redact and find_secrets(json.dumps(entry["value"])):
            return {"replayable": False, "reason": "contains a secret and redaction is on"}
        return entry

    def emit(self, event: _Event, ts: float | None = None) -> None:
        if not self.active:
            return
        if ts is not None:
            event.ts = ts
        with self._lock:
            if len(self.events) >= self.config.max_events_per_run:
                self.dropped += 1
                return
            self._seq += 1
            event.seq = self._seq
            self.events.append(event)  # type: ignore[arg-type]
        if self.stream:
            # Serialization happens in the writer thread; events are not modified after emit.
            if not get_writer().submit_data(
                ("event", str(self.store.run_dir(self.run_id)), "events.jsonl", event), self.mode
            ):
                self.dropped += 1

    def _cassette(self, kind: str, key: str, value: Any, name: str | None) -> None:
        if not (self.active and self.record_cassette):
            return
        # The exact copy is taken now (the value may change later); the secret scan and JSON
        # encoding happen in the writer thread.
        entry = make_entry(kind, key, value, name)
        get_writer().submit_data(
            ("cassette", str(self.store.run_dir(self.run_id)), Cassette.FILENAME, entry, self.config.redact),
            self.mode,
        )

    # -------------------------------------------------------------- steps
    def step_start(
        self,
        name: str,
        kind: str = "custom",
        input: Any = None,
        *,
        parent_step_id: Any = _MISSING,
        source: str | None = None,
        capture_input: bool = True,
        step_id: str | None = None,
        reads: list[str] | None = None,
        ts: float | None = None,
    ) -> str:
        step_id = step_id or new_ulid()
        if not self.active:
            return step_id
        with guard("run.step_start"):
            parent = current_step_id() if parent_step_id is _MISSING else parent_step_id
            with self._lock:
                previous = self._last_end
                if previous is not None and previous[0] == name and previous[1]:
                    self._attempts[name] = self._attempts.get(name, 1) + 1
                else:
                    self._attempts[name] = 1
                attempt = self._attempts[name]
                self._occurrences[name] = self._occurrences.get(name, 0) + 1
                self._step_names[step_id] = name
                self._step_started[step_id] = time.perf_counter()
            snap = self.snap(input) if (capture_input and self.config.capture_inputs) else None
            self._step_inputs[step_id] = snap
            self.emit(
                StepStarted(
                    run_id=self.run_id,
                    step_id=step_id,
                    parent_step_id=parent,
                    name=name,
                    kind=cast(StepKind, kind if kind in STEP_KINDS else "custom"),
                    attempt=attempt,
                    input_snapshot=snap,
                    source=source,
                    reads=reads or None,
                ),
                ts,
            )
            if ts is not None:
                self._step_ts[step_id] = ts
        return step_id

    def occurrence(self, name: str) -> int:
        return self._occurrences.get(name, 0)

    def step_end(
        self,
        step_id: str,
        output: Any = None,
        *,
        error: BaseException | tuple[str, str] | None = None,
        state_mode: Literal["replace", "update", "auto"] | None = None,
        capture_output: bool = True,
        exact_output: Any = _MISSING,
        ts: float | None = None,
    ) -> None:
        if not self.active:
            return
        with guard("run.step_end"):
            started = self._step_started.pop(step_id, None)
            duration = (time.perf_counter() - started) * 1000 if started is not None else 0.0
            start_ts = self._step_ts.pop(step_id, None)
            if ts is not None and start_ts is not None:
                duration = max(0.0, (ts - start_ts) * 1000)
            name = self._step_names.get(step_id, "")
            out_snap = self.snap(output) if capture_output else None
            before = self._step_inputs.pop(step_id, None)
            diff = None
            if state_mode == "auto":
                state_mode = (
                    "replace"
                    if isinstance(before, dict)
                    and MARK not in before
                    and isinstance(out_snap, dict)
                    and MARK not in out_snap
                    else None
                )
            if state_mode == "replace":
                diff = state_diff(before, out_snap)
            elif state_mode == "update" and isinstance(before, dict) and isinstance(out_snap, dict):
                diff = {
                    "added": sorted(k for k in out_snap if k not in before),
                    "removed": [],
                    "changed": sorted(k for k in out_snap if k in before and out_snap[k] != before[k]),
                }
            self.emit(
                StepEnded(
                    run_id=self.run_id,
                    step_id=step_id,
                    output_snapshot=out_snap,
                    state_diff=diff,
                    error=_error_info(error),
                    duration_ms=duration,
                    state_mode=state_mode,
                ),
                ts,
            )
            with self._lock:
                self._last_end = (name, error is not None)
            if exact_output is not _MISSING and error is None:
                self._cassette("step", f"{name}#{self._occurrences.get(name, 1)}", exact_output, name)

    # -------------------------------------------------------------- llm / tools
    def llm_call(
        self,
        *,
        provider: str = "unknown",
        model: str | None = None,
        input_messages: Any = None,
        output: Any = None,
        finish_reason: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        tool_calls: list[tuple[str, Any]] | None = None,
        available_tools: list[str] | None = None,
        refusal: str | None = None,
        step_id: Any = _MISSING,
        ts: float | None = None,
    ) -> None:
        if not self.active:
            return
        with guard("run.llm_call"):
            if cost_usd is None:
                from tervic.core.pricing import estimate_cost

                cost_usd = estimate_cost(model, input_tokens, output_tokens)
            if cost_usd:
                with self._lock:
                    self.total_cost += cost_usd
            self.emit(
                LLMCall(
                    run_id=self.run_id,
                    step_id=current_step_id() if step_id is _MISSING else step_id,
                    provider=provider,
                    model=model,
                    input_messages=self.snap(input_messages),
                    output=self.snap(output),
                    finish_reason=finish_reason,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    tool_calls=[ToolCallRequest(name=n, args=self.snap(a)) for n, a in (tool_calls or [])],
                    available_tools=available_tools,
                    refusal=refusal,
                ),
                ts,
            )

    def tool_call(
        self,
        tool_name: str,
        *,
        args: Any = None,
        result: Any = None,
        error: BaseException | tuple[str, str] | None = None,
        available_tools: list[str] | None = None,
        tool_schema: Any = None,
        step_id: Any = _MISSING,
        ts: float | None = None,
    ) -> None:
        if not self.active:
            return
        with guard("run.tool_call"):
            self.emit(
                ToolCall(
                    run_id=self.run_id,
                    step_id=current_step_id() if step_id is _MISSING else step_id,
                    tool_name=tool_name,
                    args=self.snap(args),
                    result=self.snap(result),
                    error=_error_info(error),
                    available_tools=available_tools,
                    tool_schema=tool_schema,
                ),
                ts,
            )

    def record_llm_response(self, key: str, response: Any) -> None:
        with guard("run.cassette_llm"):
            self._cassette("llm", key, response, None)

    def set_output(self, value: Any) -> None:
        """Record the run's final answer. Without it, the last top-level step's output is used."""
        self._final_output = value
        self._output_set = True

    # -------------------------------------------------------------- end
    def end(
        self,
        *,
        status: Literal["completed", "crashed"] = "completed",
        final_output: Any = None,
        error: BaseException | tuple[str, str] | None = None,
        wait: bool | None = None,
    ) -> None:
        if not self.active or self._ended:
            return
        self._ended = True
        unregister_active(self)
        with guard("run.end"):
            self.status = status
            output_set = final_output is not None or self._output_set
            if final_output is None:
                final_output = self._final_output
            self.emit(
                RunEnded(
                    run_id=self.run_id,
                    status=status,
                    final_output_snapshot=self.snap(final_output),
                    output_set=output_set,
                    duration_ms=(time.time() - self.started_ts) * 1000,
                    total_cost_usd=self.total_cost,
                    error=_error_info(error),
                    dropped_events=self.dropped,
                )
            )
            writer = get_writer()
            if not writer.submit_control(("finalize", self)):
                self._mark_done()
                return
            should_wait = (self.mode != "prod") if wait is None else wait
            if should_wait:
                self._done.wait(30.0)

    def _mark_done(self) -> None:
        self._done.set()

    def _finalize(self) -> None:
        """Runs in the writer thread: analyze, decide to keep (tail sampling), persist, export."""
        try:
            from tervic.analyze.engine import analyze

            report = analyze(list(self.events), self.config, self.store)
            self.report = report
            keep = (
                self.mode != "prod"
                or self.status == "crashed"
                or report.has_errors
                or random.random() < self.config.sample_rate
            )
            self.kept = keep
            if not keep:
                return
            lines = [event_to_json(e) for e in self.events]
            self.store.write_lines(self.run_id, lines)
            self.store.write_json(self.run_id, "report.json", report.model_dump(mode="json"))
            self.store.upsert(
                RunRow(
                    run_id=self.run_id,
                    name=self.name,
                    framework=self.framework,
                    verdict=report.verdict,
                    status=self.status,
                    started_at=report.started_at,
                    started_ts=self.started_ts,
                    duration_ms=report.stats.duration_ms,
                    cost_usd=report.stats.cost_usd,
                    git_sha=report.git_sha,
                    tags=self.tags,
                    errors=report.counts.get("error", 0),
                    warnings=report.counts.get("warn", 0),
                )
            )
            if self.config.retention_runs > 0:
                self.store.gc(self.config.retention_runs)
            from tervic.core.recorder import run_exporters
            from tervic.export.registry import exporters_for

            run_exporters(exporters_for(self.config), list(self.events), report)
        finally:
            self._done.set()
            from tervic.core.recorder import notify_run_listeners

            notify_run_listeners(self)

    # -------------------------------------------------------------- results
    def wait(self, timeout: float = 30.0) -> bool:
        return self._done.wait(timeout)

    @property
    def verdict(self) -> Verdict:
        """``"pass"``, ``"warn"`` or ``"fail"``. Waits for analysis if it is still running."""
        if not self.active:
            return "pass"
        if not self._ended:
            return "pass" if not self.events else self._interim_verdict()
        self._done.wait(30.0)
        return self.report.verdict if self.report is not None else "pass"

    def _interim_verdict(self) -> Verdict:
        try:
            from tervic.analyze.engine import analyze

            return analyze(list(self.events), self.config, None).verdict
        except Exception as exc:
            report_internal_error("run.interim_verdict", exc)
            return "pass"

    @property
    def findings(self) -> list[Any]:
        self._done.wait(30.0)
        return list(self.report.findings) if self.report is not None else []


def _error_info(error: BaseException | tuple[str, str] | None) -> ErrorInfo | None:
    if error is None:
        return None
    if isinstance(error, tuple):
        return ErrorInfo(type=error[0], message=error[1])
    text = "".join(tb.format_exception(type(error), error, error.__traceback__))
    return ErrorInfo(type=type(error).__name__, message=str(error), traceback=text[-4000:])


# ---------------------------------------------------------------- public context manager


class RunScope:
    """Returned by ``tervic.run(...)``; usable with ``with`` and ``async with``."""

    def __init__(
        self,
        name: str,
        tags: dict[str, str] | None = None,
        framework: str = "python",
        input: Any = _MISSING,
    ) -> None:
        self.name = name
        self.tags = tags
        self.framework = framework
        self.input = input
        self.run: Run | None = None
        self._owned = False
        self._token: contextvars.Token[Run | None] | None = None
        self.final_output: Any = None

    def __enter__(self) -> Run:
        outer = current_run()
        if outer is not None:
            self.run = outer
            return outer
        self.run = Run(self.name, framework=self.framework, tags=self.tags, input=self.input)
        self._owned = True
        self._token = _RUN.set(self.run)
        return self.run

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb_: TracebackType | None
    ) -> None:
        if not self._owned or self.run is None:
            return
        if self._token is not None:
            try:
                _RUN.reset(self._token)
            except ValueError:
                _RUN.set(None)
        if exc is not None:
            self.run.end(status="crashed", error=exc, final_output=self.final_output)
        else:
            self.run.end(status="completed", final_output=self.final_output)

    async def __aenter__(self) -> Run:
        return self.__enter__()

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb_: TracebackType | None
    ) -> None:
        self.__exit__(exc_type, exc, tb_)


def push_run(run: Run) -> contextvars.Token[Run | None]:
    return _RUN.set(run)


def pop_run(token: contextvars.Token[Run | None]) -> None:
    try:
        _RUN.reset(token)
    except ValueError:
        _RUN.set(None)


def push_step(step_id: str) -> contextvars.Token[str | None]:
    return _STEP.set(step_id)


def pop_step(token: contextvars.Token[str | None]) -> None:
    try:
        _STEP.reset(token)
    except ValueError:
        _STEP.set(None)


def searchable(value: Any) -> str:
    return text_of(value)
