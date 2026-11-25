"""Helpers shared by detector families."""

from __future__ import annotations

import re
from typing import Any

from tervic.analyze.findings import Context
from tervic.analyze.model import StepView
from tervic.core.serialize import is_empty, text_of

_KEY_ERROR = re.compile(r"""^['"]?([A-Za-z_][\w.-]*)['"]?$""")


def step_reads(ctx: Context, step: StepView) -> set[str]:
    reads = set(step.reads)
    contract = ctx.config.contracts.get(step.name)
    if contract is not None:
        reads.update(contract.reads)
    return reads


def missing_field_errors(step: StepView) -> set[str]:
    """Field names named by a KeyError / AttributeError the step raised."""
    if step.error is None:
        return set()
    names: set[str] = set()
    if step.error.type == "KeyError":
        match = _KEY_ERROR.match(step.error.message.strip())
        if match:
            names.add(match.group(1))
    elif step.error.type == "AttributeError":
        match = re.search(r"attribute '([^']+)'", step.error.message)
        if match:
            names.add(match.group(1))
    return names


def consumers(ctx: Context, step: StepView, field: str, *, until_rewritten: bool = True) -> list[StepView]:
    """Later steps that read ``field`` (statically, by contract, or by failing on it)."""
    found: list[StepView] = []
    for later in ctx.run.steps_after(step):
        if later.parent_step_id == step.step_id:
            continue
        if field in step_reads(ctx, later) or field in missing_field_errors(later):
            found.append(later)
        if until_rewritten and field in later.writes and not is_empty(later.writes[field]):
            break
    return found


def baseline_consumes(ctx: Context, step: StepView, field: str) -> bool:
    """In the baseline run, did a step after this one receive ``field`` in its input?"""
    base = ctx.baseline
    if base is None:
        return False
    match = next((s for s in base.steps if s.name == step.name and s.occurrence == step.occurrence), None)
    if match is None:
        return False
    return any(isinstance(s.input, dict) and field in s.input for s in base.steps_after(match))


def baseline_step(ctx: Context, step: StepView) -> StepView | None:
    if ctx.baseline is None:
        return None
    return next(
        (s for s in ctx.baseline.steps if s.name == step.name and s.occurrence == step.occurrence),
        None,
    ) or next((s for s in ctx.baseline.steps if s.name == step.name), None)


def describe_empty(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, str):
        return "an empty string" if value == "" else "only whitespace"
    if isinstance(value, list):
        return "an empty list"
    if isinstance(value, dict):
        return "an empty dict"
    return "an empty value"


def enabled(ctx: Context, detector_id: str, step: StepView | None) -> bool:
    return ctx.config.detector_enabled(detector_id, step.name if step else None)


def output_text(value: Any) -> str:
    return text_of(value)
