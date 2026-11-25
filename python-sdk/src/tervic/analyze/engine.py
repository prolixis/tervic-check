"""Analysis engine: run detectors, contracts, baseline diff and root cause; produce a Report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from tervic._version import __version__
from tervic.analyze.findings import SEVERITY_RANK, Context, Finding
from tervic.analyze.lineage import RootCauseGroup, attach_root_causes, group_findings
from tervic.analyze.model import RunView, build_view
from tervic.config import TervicConfig, get_config
from tervic.core.safety import internal_error_counts, report_internal_error

if TYPE_CHECKING:
    from tervic.core.events import Event
    from tervic.store.local import LocalStore

Verdict = Literal["pass", "warn", "fail"]


class Stats(BaseModel):
    steps: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: float = 0.0


class StepSummary(BaseModel):
    step_id: str
    name: str
    kind: str
    parent_step_id: str | None = None
    duration_ms: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None
    source: str | None = None
    severity: str | None = None


class Report(BaseModel):
    run_id: str
    run_name: str = ""
    framework: str = ""
    status: str | None = None
    verdict: Verdict = "pass"
    fail_on: str = "error"
    started_at: str | None = None
    git_sha: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    stats: Stats = Field(default_factory=Stats)
    counts: dict[str, int] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    groups: list[RootCauseGroup] = Field(default_factory=list)
    steps: list[StepSummary] = Field(default_factory=list)
    run_error: str | None = None
    baseline: str | None = None
    baseline_diff: dict[str, Any] | None = None
    judge: list[dict[str, Any]] = Field(default_factory=list)
    dropped_events: int = 0
    internal_errors: dict[str, int] = Field(default_factory=dict)
    tervic_version: str = __version__

    @property
    def has_errors(self) -> bool:
        return self.counts.get("error", 0) > 0 or self.status == "crashed"

    @property
    def passed(self) -> bool:
        return self.verdict != "fail"


def _detectors() -> list[Any]:
    from tervic.analyze.detectors import REGISTRY

    return [cls() for cls in REGISTRY]


def compute_verdict(findings: list[Finding], status: str | None, fail_on: str) -> Verdict:
    if status == "crashed":
        return "fail"
    threshold = SEVERITY_RANK["warn" if fail_on == "warn" else "error"]
    worst = max((SEVERITY_RANK[f.severity] for f in findings), default=0)
    if worst >= threshold:
        return "fail"
    if worst >= SEVERITY_RANK["warn"]:
        return "warn"
    return "pass"


def analyze(
    events: list[Event],
    config: TervicConfig | None = None,
    store: LocalStore | None = None,
    *,
    baseline_events: list[Event] | None = None,
    baseline_name: str | None = None,
    use_judge: bool | None = None,
) -> Report:
    config = config or get_config()
    view = build_view(events)
    baseline_view: RunView | None = None
    if baseline_events is not None:
        baseline_view = build_view(baseline_events)
    elif store is not None and view.name:
        try:
            found = store.baseline_for_run_name(view.name)
        except Exception as exc:
            report_internal_error("engine.baseline_lookup", exc)
            found = None
        if found is not None:
            baseline_name, events_b = found
            baseline_view = build_view(events_b)
    if baseline_view is not None and baseline_view.run_id == view.run_id:
        baseline_view = None
    ctx = Context(run=view, config=config, baseline=baseline_view, baseline_name=baseline_name)

    findings: list[Finding] = []
    for detector in _detectors():
        try:
            for finding in detector.run(ctx):
                override = config.detectors.severity.get(finding.detector_id)
                if override is not None:
                    finding.severity = override
                findings.append(finding)
        except Exception as exc:
            report_internal_error(f"detector.{detector.id}", exc)

    try:
        attach_root_causes(view, findings, config)
    except Exception as exc:
        report_internal_error("engine.root_cause", exc)
    groups: list[RootCauseGroup] = []
    try:
        groups = group_findings(view, findings)
    except Exception as exc:
        report_internal_error("engine.grouping", exc)

    judge_results: list[dict[str, Any]] = []
    if use_judge if use_judge is not None else config.judge.enabled:
        try:
            from tervic.analyze.judge import judge_run

            judge_findings, judge_results = judge_run(view, findings, config)
            findings.extend(judge_findings)
            if judge_findings:
                attach_root_causes(view, judge_findings, config)
                groups = group_findings(view, findings)
        except Exception as exc:
            report_internal_error("engine.judge", exc)

    counts = {"error": 0, "warn": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    severity_by_step: dict[str, str] = {}
    for finding in findings:
        if finding.step_id:
            current = severity_by_step.get(finding.step_id)
            if current is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[current]:
                severity_by_step[finding.step_id] = finding.severity

    steps = [
        StepSummary(
            step_id=s.step_id,
            name=s.name,
            kind=s.kind,
            parent_step_id=s.parent_step_id,
            duration_ms=round(s.duration_ms, 3),
            cost_usd=sum(c.cost_usd or 0.0 for c in s.llm_calls),
            error=f"{s.error.type}: {s.error.message}" if s.error else None,
            source=s.source,
            severity=severity_by_step.get(s.step_id),
        )
        for s in view.steps
    ]
    diff = ctx.extra.get("baseline_diff")
    return Report(
        run_id=view.run_id,
        run_name=view.name,
        framework=view.framework,
        status=view.status,
        verdict=compute_verdict(findings, view.status, config.fail_on),
        fail_on=config.fail_on,
        started_at=view.started_at,
        git_sha=view.git_sha,
        tags=view.tags,
        stats=Stats(
            steps=len(view.steps),
            llm_calls=len(view.llm_calls),
            tool_calls=len(view.tool_calls),
            input_tokens=sum(c.input_tokens or 0 for c in view.llm_calls),
            output_tokens=sum(c.output_tokens or 0 for c in view.llm_calls),
            cost_usd=round(view.total_cost, 6),
            duration_ms=round(view.duration_ms, 3),
        ),
        counts=counts,
        findings=findings,
        groups=groups,
        steps=steps,
        run_error=f"{view.error.type}: {view.error.message}" if view.error else None,
        baseline=baseline_name if baseline_view is not None else None,
        baseline_diff=diff.model_dump() if diff is not None else None,
        judge=judge_results,
        dropped_events=view.dropped_events,
        internal_errors=internal_error_counts(),
    )
