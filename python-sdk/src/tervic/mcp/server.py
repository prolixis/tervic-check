"""``tervic mcp``: a stdio MCP server so Claude Code / Cursor can ask Tervic what broke.

Uses the official ``mcp`` Python SDK (``pip install tervic[mcp]``). ``mcp>=2`` exposes
``mcp.server.mcpserver.MCPServer``; 1.x called it ``FastMCP``. Both are supported.
All tools are read-only except ``run_replay``.
"""

from __future__ import annotations

from typing import Any

INSTRUCTIONS = (
    "Tervic records AI agent runs in this repo. When the user reports wrong agent output, call "
    "get_findings first, then get_step for the root-cause step, fix the code, and call run_replay to verify."
)


def _server_class() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # type: ignore[attr-defined,unused-ignore]

        return FastMCP


def _store() -> Any:
    from tervic.api import store_for

    return store_for()


def list_runs(limit: int = 20, verdict: str | None = None) -> list[dict[str, Any]]:
    """Recent runs, newest first. verdict: optional filter (pass | warn | fail)."""
    return [row.model_dump() for row in _store().list_runs(limit=max(1, min(limit, 200)), verdict=verdict)]


def get_findings(run_id: str = "last") -> dict[str, Any]:
    """Findings for a run, grouped by root cause, with the root-cause step's file:line."""
    from tervic.api import check

    report = check(run_id, write=False)
    return {
        "run_id": report.run_id,
        "run_name": report.run_name,
        "verdict": report.verdict,
        "status": report.status,
        "counts": report.counts,
        "root_causes": [
            {
                "root_cause_step": g.root_cause_step_name,
                "source": g.source,
                "severity": g.severity,
                "detectors": g.detector_ids,
                "summary": g.title,
                "how_to_fix": g.primary.how_to_fix,
                "findings": [
                    f.model_dump(
                        include={"detector_id", "severity", "step_name", "field_path", "message", "evidence"}
                    )
                    for f in g.findings
                ],
            }
            for g in report.groups
        ],
    }


def get_step(run_id: str, step_name: str) -> dict[str, Any]:
    """Input/output snapshots, state diff, LLM and tool calls for every occurrence of one step."""
    from tervic.analyze.model import build_view

    store = _store()
    view = build_view(store.load_events(store.resolve(run_id)))
    steps = [s for s in view.steps if s.name == step_name]
    if not steps:
        return {
            "error": f"No step named {step_name!r}.",
            "steps": list(dict.fromkeys(s.name for s in view.steps)),
        }
    return {
        "run_id": view.run_id,
        "occurrences": [
            {
                "occurrence": s.occurrence,
                "kind": s.kind,
                "source": s.source,
                "duration_ms": s.duration_ms,
                "input": s.input,
                "output": s.output,
                "state_diff": s.state_diff,
                "error": s.error.model_dump() if s.error else None,
                "llm_calls": [
                    c.model_dump(
                        mode="json",
                        include={
                            "model",
                            "finish_reason",
                            "input_tokens",
                            "output_tokens",
                            "output",
                            "tool_calls",
                        },
                    )
                    for c in s.llm_calls
                ],
                "tool_calls": [
                    t.model_dump(mode="json", include={"tool_name", "args", "result", "error"})
                    for t in s.tool_calls
                ],
            }
            for s in steps
        ],
    }


def get_fix_prompt(run_id: str = "last") -> str:
    """The same ready-to-paste prompt as `tervic fix`."""
    from tervic.analyze.model import build_view
    from tervic.api import check
    from tervic.fix.prompt import build_prompt

    store = _store()
    resolved = store.resolve(run_id)
    report = check(resolved, write=False)
    baseline = None
    if report.baseline:
        events = store.baseline_events(report.baseline)
        baseline = build_view(events) if events else None
    return build_prompt(report, build_view(store.load_events(resolved)), baseline, "claude")


def diff_runs(a: str, b: str = "last") -> dict[str, Any]:
    """Structural / sequence / cost diff between two runs ('baseline:<name>' is accepted)."""
    from tervic.analyze.baseline import diff_runs as do_diff
    from tervic.analyze.model import build_view
    from tervic.config import get_config

    store = _store()

    def view(ref: str) -> Any:
        if ref.startswith("baseline:"):
            events = store.baseline_events(ref.split(":", 1)[1])
            if events is None:
                raise ValueError(f"No baseline {ref!r}")
            return build_view(events)
        return build_view(store.load_events(store.resolve(ref)))

    return do_diff(view(a), view(b), get_config().detectors.baseline_regression_pct).model_dump()


def run_replay(run_id: str, from_step: str | None = None, offline: bool = False) -> dict[str, Any]:
    """Replay a run from a step with the current code and return the new verdict (not read-only)."""
    from tervic.replay.replayer import replay

    return replay(run_id, from_step, offline=offline).model_dump()


TOOLS = (list_runs, get_findings, get_step, get_fix_prompt, diff_runs, run_replay)
READ_ONLY = {"list_runs", "get_findings", "get_step", "get_fix_prompt", "diff_runs"}


def build_server() -> Any:
    server_cls = _server_class()
    server = server_cls(name="tervic", instructions=INSTRUCTIONS)
    from mcp.types import ToolAnnotations

    for fn in TOOLS:
        # camelCase keys validate on mcp 1.x (field names) and 2.x (aliases of snake_case fields)
        annotations = ToolAnnotations.model_validate(
            {
                "readOnlyHint": fn.__name__ in READ_ONLY,
                "destructiveHint": False,
                "openWorldHint": False,
            }
        )
        server.tool(name=fn.__name__, description=(fn.__doc__ or "").strip(), annotations=annotations)(fn)
    return server


def run_stdio() -> None:
    build_server().run(transport="stdio")
