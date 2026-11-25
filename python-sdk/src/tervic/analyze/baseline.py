"""Golden-run diffs: structure, step sequence and cost/latency/tokens. No LLM involved."""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel, Field

from tervic.analyze.model import RunView, StepView
from tervic.core.serialize import MARK, is_empty, item_count, value_type


class StepChange(BaseModel):
    step: str
    field: str | None = None
    change: str
    before: Any = None
    after: Any = None


class MetricChange(BaseModel):
    metric: str
    before: float
    after: float
    pct: float | None = None
    regression: bool = False


class RunDiff(BaseModel):
    a: str
    b: str
    sequence_a: list[str] = Field(default_factory=list)
    sequence_b: list[str] = Field(default_factory=list)
    steps_removed: list[str] = Field(default_factory=list)
    steps_added: list[str] = Field(default_factory=list)
    order_changed: bool = False
    changes: list[StepChange] = Field(default_factory=list)
    metrics: list[MetricChange] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.steps_removed
            or self.steps_added
            or self.order_changed
            or self.changes
            or any(m.regression for m in self.metrics)
        )


def _fields(step: StepView) -> dict[str, Any] | None:
    if step.state_mode in ("update", "replace") and isinstance(step.output, dict):
        return step.writes if step.state_mode == "update" else step.output
    if isinstance(step.output, dict) and MARK not in step.output:
        return step.output
    return None


def _value_changes(name: str, field: str | None, before: Any, after: Any) -> list[StepChange]:
    changes: list[StepChange] = []
    if before is None and after is None:
        return changes
    if not is_empty(before) and is_empty(after):
        changes.append(
            StepChange(
                step=name, field=field, change="became_empty", before=_summary(before), after=_summary(after)
            )
        )
    elif is_empty(before) and not is_empty(after):
        changes.append(
            StepChange(
                step=name,
                field=field,
                change="became_non_empty",
                before=_summary(before),
                after=_summary(after),
            )
        )
    elif before is not None and after is not None and value_type(before) != value_type(after):
        changes.append(
            StepChange(
                step=name,
                field=field,
                change="type_changed",
                before=value_type(before),
                after=value_type(after),
            )
        )
    return changes


def _summary(value: Any) -> Any:
    count = item_count(value)
    if count is not None:
        return f"{count} items"
    if isinstance(value, str):
        return f"{len(value)} chars"
    if isinstance(value, dict) and MARK not in value:
        return f"{len(value)} keys"
    return value


def diff_runs(base: RunView, current: RunView, regression_pct: float = 50.0) -> RunDiff:
    seq_a = [s.name for s in base.steps]
    seq_b = [s.name for s in current.steps]
    diff = RunDiff(a=base.run_id, b=current.run_id, sequence_a=seq_a, sequence_b=seq_b)
    names_a, names_b = set(seq_a), set(seq_b)
    diff.steps_removed = [n for n in dict.fromkeys(seq_a) if n not in names_b]
    diff.steps_added = [n for n in dict.fromkeys(seq_b) if n not in names_a]
    common_a = [n for n in seq_a if n in names_b]
    common_b = [n for n in seq_b if n in names_a]
    if common_a != common_b:
        matcher = difflib.SequenceMatcher(a=common_a, b=common_b, autojunk=False)
        diff.order_changed = any(tag != "equal" for tag, *_ in matcher.get_opcodes())

    by_key = {(s.name, s.occurrence): s for s in current.steps}
    for old in base.steps:
        new = by_key.get((old.name, old.occurrence))
        if new is None or old.error is not None:
            continue
        old_fields, new_fields = _fields(old), _fields(new)
        if old_fields is not None and new_fields is not None:
            for field in old_fields:
                if field not in new_fields:
                    diff.changes.append(
                        StepChange(
                            step=old.name, field=field, change="removed", before=_summary(old_fields[field])
                        )
                    )
                else:
                    diff.changes.extend(_value_changes(old.name, field, old_fields[field], new_fields[field]))
            for field in new_fields:
                if field not in old_fields:
                    diff.changes.append(
                        StepChange(
                            step=old.name, field=field, change="added", after=_summary(new_fields[field])
                        )
                    )
        else:
            diff.changes.extend(_value_changes(old.name, None, old.output, new.output))

    for metric, before, after in (
        ("cost_usd", base.total_cost, current.total_cost),
        ("duration_ms", base.duration_ms, current.duration_ms),
        ("tokens", float(base.tokens), float(current.tokens)),
    ):
        pct = ((after - before) / before * 100.0) if before else None
        diff.metrics.append(
            MetricChange(
                metric=metric,
                before=before,
                after=after,
                pct=pct,
                regression=pct is not None and pct > regression_pct,
            )
        )
    return diff
