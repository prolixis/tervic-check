"""``tervic replay <run_id> --from <step>``.

Re-runs the agent through its entry point (``[replay] entrypoint = "app.main:run"``). Steps before
``--from`` are served from the run's cassette (no LLM calls, no cost); steps from ``--from`` onward
run live with the current code. With ``--offline`` every LLM call made through a client wrapped by
``tervic.attach_llm`` is served from the cassette as well. Afterwards the new run is checked and
compared with the original.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tervic.config import TervicConfig, get_config
from tervic.core.events import RunStarted
from tervic.core.run import ReplayState, RunScope, set_replay_state
from tervic.store.cassette import Cassette, decode
from tervic.store.local import LocalStore


class ReplayError(RuntimeError):
    pass


class ReplayResult(BaseModel):
    original_run_id: str
    new_run_id: str | None = None
    from_step: str | None = None
    offline: bool = False
    cassette_hits: int = 0
    cassette_misses: int = 0
    live: list[str] = Field(default_factory=list)
    before_verdict: str | None = None
    after_verdict: str | None = None
    before_counts: dict[str, int] = Field(default_factory=dict)
    after_counts: dict[str, int] = Field(default_factory=dict)
    fixed: list[str] = Field(default_factory=list)
    still_failing: list[str] = Field(default_factory=list)
    new: list[str] = Field(default_factory=list)
    error: str | None = None


def load_entrypoint(ref: str, root: Path | None = None) -> Any:
    """Import ``"package.module:function"``. The project root is put on ``sys.path`` first."""
    module_name, sep, attr = ref.partition(":")
    if not sep or not module_name or not attr:
        raise ReplayError(f"Entry point must look like 'package.module:function', got {ref!r}.")
    base = str((root or Path.cwd()).resolve())
    if base not in sys.path:
        sys.path.insert(0, base)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise ReplayError(f"Could not import {module_name!r}: {type(exc).__name__}: {exc}") from exc
    target: Any = module
    for part in attr.split("."):
        if not hasattr(target, part):
            raise ReplayError(f"{module_name!r} has no attribute {attr!r}.")
        target = getattr(target, part)
    if not callable(target):
        raise ReplayError(f"Entry point {ref!r} is not callable.")
    return target


def _keys(report: Any) -> set[str]:
    return {f"{f.detector_id}:{f.step_name or '-'}:{f.field_path or '-'}" for f in report.findings}


def replay(
    run_ref: str | None,
    from_step: str | None = None,
    *,
    offline: bool = False,
    entrypoint: str | None = None,
    config: TervicConfig | None = None,
) -> ReplayResult:
    from tervic.api import check

    config = config or get_config()
    store = LocalStore(config.store_path())
    run_id = store.resolve(run_ref)
    events = store.load_events(run_id)
    started = next((e for e in events if isinstance(e, RunStarted)), None)
    if started is None:
        raise ReplayError(f"Run {run_id} has no run_started event.")
    step_names = [getattr(e, "name", None) for e in events if getattr(e, "type", "") == "step_started"]
    if from_step is not None and from_step not in step_names:
        known = ", ".join(dict.fromkeys(n for n in step_names if n)) or "none"
        raise ReplayError(f"Step {from_step!r} is not in run {run_id}. Steps: {known}.")
    ref = entrypoint or config.replay.entrypoint
    if not ref:
        raise ReplayError(
            'No entry point. Set [replay] entrypoint = "package.module:function" in tervic.toml '
            "or pass --entrypoint."
        )
    fn = load_entrypoint(ref, Path(config.root))
    cassette_path = store.run_dir(run_id) / Cassette.FILENAME
    if not cassette_path.is_file() and offline:
        raise ReplayError(
            f"Run {run_id} has no cassette, so --offline cannot serve LLM calls "
            "(recording is off in prod mode by default)."
        )
    cassette = Cassette.load(cassette_path)
    if not cassette_path.is_file():
        cassette.live.append("everything (this run has no cassette, so all steps ran live)")

    before = check(run_id, config=config, write=False)
    result = ReplayResult(
        original_run_id=run_id,
        from_step=from_step,
        offline=offline,
        before_verdict=before.verdict,
        before_counts=before.counts,
    )
    args: tuple[Any, ...] = ()
    if started.entry_input is not None:
        args = (decode(started.entry_input),)
    elif started.input_snapshot is not None:
        args = (started.input_snapshot,)

    set_replay_state(ReplayState(cassette, from_step, offline))
    scope = RunScope(
        started.name,
        tags={**started.tags, "replay_of": run_id, "replay_from": from_step or ""},
        framework=started.framework,
        input=args[0] if args else None,
    )
    try:
        with scope as new_run:
            output = fn(*args)
            if inspect.isawaitable(output):
                output = asyncio.run(_await(output))
            new_run.set_output(output)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        set_replay_state(None)

    finished = scope.run
    if finished is not None:
        finished.wait()
        result.new_run_id = finished.run_id
        after = finished.report
        if after is not None:
            result.after_verdict = after.verdict
            result.after_counts = after.counts
            old_keys, new_keys = _keys(before), _keys(after)
            result.fixed = sorted(old_keys - new_keys)
            result.still_failing = sorted(old_keys & new_keys)
            result.new = sorted(new_keys - old_keys)
    result.cassette_hits = cassette.hits
    result.cassette_misses = cassette.misses
    result.live = list(dict.fromkeys(cassette.live))
    return result


async def _await(value: Any) -> Any:
    return await value
