"""7.1 Output integrity: TV001–TV007."""

from __future__ import annotations

from collections.abc import Iterator

from tervic.analyze.contracts import check_step
from tervic.analyze.detectors._util import (
    baseline_consumes,
    baseline_step,
    consumers,
    describe_empty,
    enabled,
    step_reads,
)
from tervic.analyze.findings import Context, Detector, Finding, Severity, preview, register
from tervic.core.serialize import is_empty, value_type


@register
class EmptyOutput(Detector):
    id = "TV001"
    name = "empty-output"
    severity = "error"
    description = (
        "A step returned None, {}, [], an empty string or whitespace when it normally returns content."
    )
    how_to_fix = (
        "Find why the step produced nothing. If an empty result is legitimate, return an explicit "
        "'no result' value the next step handles, or disable TV001 for this step."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if not step.ended or step.error is not None or step.kind in ("retriever", "llm"):
                continue
            if not enabled(ctx, self.id, step) or not is_empty(step.output):
                continue
            reason = None
            severity: Severity = self.severity
            base = baseline_step(ctx, step)
            others = [
                s
                for s in ctx.run.steps
                if s.name == step.name
                and s is not step
                and s.ended
                and s.error is None
                and not is_empty(s.output)
            ]
            if base is not None and not is_empty(base.output):
                reason = f"the baseline run returned {preview(base.output, 60)}"
            elif others:
                reason = f"another call of `{step.name}` in this run returned content"
            elif step.state_mode in ("update", "replace"):
                reason = "it is a graph step and it wrote nothing to the state"
                severity = "warn"
            elif step.output is not None:
                reason = "it returned an explicitly empty value"
                severity = "warn"
            if reason is None:
                continue
            yield self.finding(
                f"`{step.name}` returned {describe_empty(step.output)}, but {reason}.",
                step=step,
                evidence={"output": step.output},
                severity=severity,
            )


@register
class DroppedField(Detector):
    id = "TV002"
    name = "dropped-field"
    severity = "error"
    description = "A state field present before the step is missing after it, and a later step reads it."
    how_to_fix = (
        "Make the step return the full state (for example `{**state, 'new': value}`) instead of a new "
        "dict that leaves fields out."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if step.state_mode != "replace" or not step.state_diff or not enabled(ctx, self.id, step):
                continue
            for field in step.state_diff.get("removed", []):
                readers = consumers(ctx, step, field)
                in_baseline = baseline_consumes(ctx, step, field)
                if not readers and not in_baseline:
                    continue
                who = f"`{readers[0].name}`" if readers else "a later step (in the baseline run)"
                yield self.finding(
                    f"`{step.name}` dropped `{field}` from the state, but {who} reads it.",
                    step=step,
                    field_path=field,
                    evidence={
                        "before": (step.state_before or {}).get(field),
                        "readers": [s.name for s in readers],
                    },
                )


@register
class NullField(Detector):
    id = "TV003"
    name = "null-field"
    severity = "error"
    description = "A field was written as None or empty, and a later step reads it."
    how_to_fix = (
        "Find why the step wrote an empty value. Raise or return an explicit error state instead of "
        "passing an empty field downstream."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if step.error is not None or not enabled(ctx, self.id, step):
                continue
            for field, value in step.writes.items():
                if not is_empty(value):
                    continue
                readers = consumers(ctx, step, field)
                if not readers:
                    continue
                yield self.finding(
                    f"`{step.name}` wrote `{field}` as {describe_empty(value)}, and `{readers[0].name}` "
                    "reads it.",
                    step=step,
                    field_path=field,
                    evidence={"value": value, "readers": [s.name for s in readers]},
                )


@register
class NeverWritten(Detector):
    id = "TV004"
    name = "never-written"
    severity = "error"
    description = "A step reads a field that no earlier step wrote and the run input did not contain."
    how_to_fix = "Make sure an earlier step writes the field, or pass it in the run input."

    def run(self, ctx: Context) -> Iterator[Finding]:
        run_input = ctx.run.input if isinstance(ctx.run.input, dict) else {}
        for step in ctx.run.steps:
            if not isinstance(step.input, dict) or not enabled(ctx, self.id, step):
                continue
            for field in sorted(step_reads(ctx, step)):
                if field in step.input or field in run_input:
                    continue
                written = any(field in s.writes for s in ctx.run.steps_before(step))
                if written:
                    continue
                yield self.finding(
                    f"`{step.name}` reads `{field}`, but no earlier step wrote it and the run input "
                    "does not contain it.",
                    step=step,
                    field_path=field,
                    evidence={"input_fields": sorted(step.input)[:30]},
                )


@register
class TypeDrift(Detector):
    id = "TV005"
    name = "type-drift"
    severity = "warn"
    description = "A field changes type between steps (e.g. list → str), or differs from the baseline run."
    how_to_fix = "Keep each state field a single type. Convert at the boundary, not halfway through."

    def run(self, ctx: Context) -> Iterator[Finding]:
        last_type: dict[str, tuple[str, str]] = {}
        for field, value in (ctx.run.input or {}).items() if isinstance(ctx.run.input, dict) else []:
            if value is not None:
                last_type[field] = (value_type(value), "run input")
        for step in ctx.run.steps:
            if step.error is not None:
                continue
            for field, value in step.writes.items():
                if value is None:
                    continue
                current = value_type(value)
                previous = last_type.get(field)
                if previous and previous[0] != current and enabled(ctx, self.id, step):
                    yield self.finding(
                        f"`{step.name}` changed `{field}` from {previous[0]} (set by {previous[1]}) to "
                        f"{current}.",
                        step=step,
                        field_path=field,
                        evidence={"previous_type": previous[0], "new_type": current, "value": value},
                    )
                last_type[field] = (current, f"`{step.name}`")
                base = baseline_step(ctx, step)
                if base is None or not enabled(ctx, self.id, step):
                    continue
                base_value = base.writes.get(field)
                if (
                    base_value is not None
                    and value_type(base_value) != current
                    and not (previous and previous[0] != current)
                ):
                    yield self.finding(
                        f"`{step.name}` wrote `{field}` as {current}; in the baseline run it was "
                        f"{value_type(base_value)}.",
                        step=step,
                        field_path=field,
                        evidence={"baseline_type": value_type(base_value), "new_type": current},
                    )


@register
class SchemaViolation(Detector):
    id = "TV006"
    name = "schema-violation"
    severity = "error"
    description = "A step's output fails the pydantic model or JSON schema declared in its contract."
    how_to_fix = (
        "Fix the step so its output matches the schema, or update the schema if it changed on purpose."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        yield from _contract_findings(self, ctx, "TV006")


@register
class ContractViolation(Detector):
    id = "TV007"
    name = "contract-violation"
    severity = "error"
    description = "A step broke its contract: a required field was not read, written, non-empty, long enough or of the declared type."
    how_to_fix = "Fix the step so it meets its contract, or update the contract in tervic.toml if the change is intended."

    def run(self, ctx: Context) -> Iterator[Finding]:
        yield from _contract_findings(self, ctx, "TV007")


def _contract_findings(detector: Detector, ctx: Context, detector_id: str) -> Iterator[Finding]:
    for step in ctx.run.steps:
        spec = ctx.config.contracts.get(step.name)
        if spec is None or not step.ended or not enabled(ctx, detector_id, step):
            continue
        if step.error is not None:
            continue
        for violation in check_step(step, spec):
            if violation.detector_id != detector_id:
                continue
            yield detector.finding(
                violation.message,
                step=step,
                field_path=violation.field,
                evidence=violation.evidence,
            )
