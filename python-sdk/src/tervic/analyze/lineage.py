"""Field provenance and root cause.

For every field we know which step wrote each value and which steps read it. For each finding we
walk back to the earliest step where the bad value (empty, wrong type, error text, missing) first
appeared, then group findings that share that origin: one problem, not ten symptoms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from tervic.analyze.findings import SEVERITY_RANK, Finding, Severity
from tervic.analyze.model import RunView, StepView
from tervic.config import TervicConfig
from tervic.core.serialize import is_empty, value_type


@dataclass
class Write:
    step: StepView
    value: Any


@dataclass
class Provenance:
    writes: dict[str, list[Write]] = field(default_factory=dict)
    reads: dict[str, list[StepView]] = field(default_factory=dict)

    def last_write_before(self, name: str, step: StepView, inclusive: bool = False) -> Write | None:
        best: Write | None = None
        for write in self.writes.get(name, []):
            if write.step.index < step.index or (inclusive and write.step.index == step.index):
                best = write
        return best


def build_provenance(run: RunView, config: TervicConfig) -> Provenance:
    prov = Provenance()
    for step in run.steps:
        for name, value in step.writes.items():
            prov.writes.setdefault(name, []).append(Write(step, value))
        reads = set(step.reads)
        contract = config.contracts.get(step.name)
        if contract is not None:
            reads.update(contract.reads)
        for name in reads:
            prov.reads.setdefault(name, []).append(step)
    return prov


def _bad(value: Any, previous: Any = None) -> bool:
    from tervic.analyze.detectors.swallowed_errors import error_in

    if is_empty(value) or error_in(value) is not None:
        return True
    return previous is not None and value is not None and value_type(previous) != value_type(value)


def _step_reads(step: StepView, config: TervicConfig) -> set[str]:
    reads = set(step.reads)
    contract = config.contracts.get(step.name)
    if contract is not None:
        reads.update(contract.reads)
    if not reads and isinstance(step.input, dict) and step.kind in ("retriever", "llm", "tool"):
        reads = set(step.input)
    return reads


def _trace(step: StepView, prov: Provenance, config: TervicConfig, depth: int = 0) -> StepView:
    """Follow bad inputs upstream to the step that first produced them."""
    if depth > 12 or not isinstance(step.input, dict):
        return step
    origin = step
    for name in sorted(_step_reads(step, config)):
        if name not in step.input or not _bad(step.input[name]):
            continue
        write = prov.last_write_before(name, step)
        if write is None or not _bad(write.value):
            continue
        writer = _earliest_bad_write(name, write, prov)
        upstream = _trace(writer.step, prov, config, depth + 1)
        if upstream.index < origin.index:
            origin = upstream
    return origin


def _earliest_bad_write(name: str, write: Write, prov: Provenance) -> Write:
    writes = [w for w in prov.writes.get(name, []) if w.step.index <= write.step.index]
    earliest = write
    for candidate in reversed(writes):
        if _bad(candidate.value):
            earliest = candidate
        else:
            break
    return earliest


def root_cause_step(
    finding: Finding, run: RunView, prov: Provenance, config: TervicConfig
) -> StepView | None:
    step = run.step(finding.step_id)
    if step is None:
        return None
    if finding.field_path:
        name = finding.field_path.split(".")[0].split("[")[0]
        write = prov.last_write_before(name, step, inclusive=True)
        if write is not None and _bad(write.value):
            step = _earliest_bad_write(name, write, prov).step
    return _trace(step, prov, config)


def attach_root_causes(run: RunView, findings: list[Finding], config: TervicConfig) -> None:
    prov = build_provenance(run, config)
    for finding in findings:
        root = root_cause_step(finding, run, prov, config)
        if root is not None:
            finding.root_cause_step_id = root.step_id
            finding.root_cause_step_name = root.name
            if finding.source is None and root.step_id == finding.step_id:
                finding.source = root.source


# Data-flow detectors that describe a symptom generically; a more specific finding at the same
# step (e.g. TV301 empty-retrieval) makes a better headline.
_GENERIC = frozenset({"TV001", "TV003", "TV004", "TV005", "TV701"})


class RootCauseGroup(BaseModel):
    root_cause_step_id: str | None = None
    root_cause_step_name: str | None = None
    source: str | None = None
    severity: Severity = "info"
    title: str
    detector_ids: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    @property
    def primary(self) -> Finding:
        return self.findings[0]

    @property
    def symptoms(self) -> list[Finding]:
        return self.findings[1:]


def group_findings(run: RunView, findings: list[Finding]) -> list[RootCauseGroup]:
    buckets: dict[str | None, list[Finding]] = {}
    for finding in findings:
        buckets.setdefault(finding.root_cause_step_id, []).append(finding)
    groups: list[RootCauseGroup] = []
    for root_id, items in buckets.items():
        root = run.step(root_id)

        def order(f: Finding, root_id: str | None = root_id) -> tuple[int, int, int, int]:
            at_root = 0 if f.step_id == root_id else 1
            step = run.step(f.step_id)
            generic = 1 if f.detector_id in _GENERIC else 0
            return (at_root, -SEVERITY_RANK[f.severity], generic, step.index if step else 10**6)

        items.sort(key=order)
        severity = max((f.severity for f in items), key=lambda s: SEVERITY_RANK[s])
        groups.append(
            RootCauseGroup(
                root_cause_step_id=root_id,
                root_cause_step_name=root.name if root else None,
                source=root.source if root else None,
                severity=severity,
                title=items[0].message,
                detector_ids=list(dict.fromkeys(f.detector_id for f in items)),
                findings=items,
            )
        )

    def group_order(group: RootCauseGroup) -> tuple[int, int]:
        root = run.step(group.root_cause_step_id)
        return (-SEVERITY_RANK[group.severity], root.index if root is not None else 10**6)

    groups.sort(key=group_order)
    return groups
