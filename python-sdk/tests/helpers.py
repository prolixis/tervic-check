"""Helpers to build recorded runs directly, without a framework."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tervic.analyze.engine import Report, analyze
from tervic.config import TervicConfig, get_config
from tervic.core.run import Run, RunScope


def record(body: Callable[[Run], Any], name: str = "test-run", **kwargs: Any) -> Report:
    """Run ``body(run)`` inside ``tervic.run(name)`` and return the analyzed report."""
    scope = RunScope(name, **kwargs)
    with scope as run:
        output = body(run)
        if output is not None:
            run.set_output(output)
    run.wait()
    assert run.report is not None
    return run.report


def node(
    run: Run,
    name: str,
    before: Any,
    after: Any,
    *,
    kind: str = "node",
    mode: str | None = "update",
    reads: list[str] | None = None,
    error: BaseException | tuple[str, str] | None = None,
    parent: Any = None,
) -> str:
    kwargs: dict[str, Any] = {"reads": reads}
    if parent is not None:
        kwargs["parent_step_id"] = parent
    step_id = run.step_start(name, kind, before, **kwargs)
    run.step_end(step_id, after, state_mode=mode, error=error)  # type: ignore[arg-type]
    return step_id


def ids(report: Report) -> set[str]:
    return {f.detector_id for f in report.findings}


def findings(report: Report, detector_id: str) -> list[Any]:
    return [f for f in report.findings if f.detector_id == detector_id]


def reanalyze(report_events: list[Any], config: TervicConfig | None = None, **kwargs: Any) -> Report:
    return analyze(report_events, config or get_config(), None, **kwargs)
