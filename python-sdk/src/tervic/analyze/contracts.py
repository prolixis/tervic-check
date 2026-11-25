"""Contracts: what each step must read and produce.

Declare them in code with ``tervic.contract(...)`` or in ``tervic.toml`` under ``[contracts.<step>]``.
Violations become error-severity findings (TV006 for schemas, TV007 for everything else).
``tervic contracts infer`` proposes contracts from recent successful runs.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tervic.analyze.model import RunView, StepView
from tervic.config import Contract, add_contract
from tervic.core.serialize import MARK, is_empty, is_marker, value_type


def contract(
    step: str,
    *,
    reads: Iterable[str] = (),
    writes: Iterable[str] = (),
    non_empty: Iterable[str] = (),
    schema: str | dict[str, Any] | type | None = None,
    min_length: dict[str, int] | None = None,
    max_length: dict[str, int] | None = None,
    types: dict[str, str] | None = None,
) -> Contract:
    """Declare a contract for ``step`` from code (highest precedence, same as ``configure``)."""
    schema_ref: str | dict[str, Any] | None
    schema_ref = f"{schema.__module__}:{schema.__qualname__}" if isinstance(schema, type) else schema
    built = Contract(
        step=step,
        reads=list(reads),
        writes=list(writes),
        non_empty=list(non_empty),
        schema=schema_ref,
        min_length=dict(min_length or {}),
        max_length=dict(max_length or {}),
        types=dict(types or {}),
    )
    add_contract(built)
    return built


@dataclass
class Violation:
    detector_id: str
    field: str | None
    message: str
    evidence: dict[str, Any]


def _length(value: Any) -> int | None:
    if isinstance(value, str | list):
        return len(value)
    if is_marker(value, "str"):
        return int(value.get("len", 0))
    if isinstance(value, dict) and MARK not in value:
        return len(value)
    return None


def produced(step: StepView) -> dict[str, Any] | None:
    """The fields a step produced (graph steps: its update/state; others: a dict output)."""
    if step.state_mode == "update" and isinstance(step.output, dict):
        return step.output
    if isinstance(step.output, dict) and MARK not in step.output:
        return step.output
    return None


def check_step(step: StepView, spec: Contract) -> list[Violation]:
    out: list[Violation] = []
    fields = produced(step)
    state = step.state_after or fields or {}
    before = step.state_before or {}

    for name in spec.reads:
        if not isinstance(step.input, dict):
            break
        if name not in before:
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{step.name}` must read `{name}`, but its input has no `{name}` field.",
                    {"input_fields": sorted(before)[:30]},
                )
            )
        elif is_empty(before[name]):
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{step.name}` must read `{name}`, but it was empty when the step started.",
                    {"value": before[name]},
                )
            )
    for name in spec.writes:
        if fields is None or name not in fields:
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{step.name}` must write `{name}`, but it did not.",
                    {"written": sorted(fields)[:30] if fields else []},
                )
            )
    for name in spec.non_empty:
        if name in state and is_empty(state[name]):
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{name}` must be non-empty after `{step.name}`, but it is empty.",
                    {"value": state[name]},
                )
            )
        elif name not in state and name not in spec.writes:
            out.append(
                Violation(
                    "TV007", name, f"`{name}` must be non-empty after `{step.name}`, but it is missing.", {}
                )
            )
    for name, minimum in spec.min_length.items():
        length = _length(state.get(name))
        if name in state and length is not None and length < minimum:
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{name}` must have length ≥ {minimum} after `{step.name}`, got {length}.",
                    {"length": length},
                )
            )
    for name, maximum in spec.max_length.items():
        length = _length(state.get(name))
        if length is not None and length > maximum:
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{name}` must have length ≤ {maximum} after `{step.name}`, got {length}.",
                    {"length": length},
                )
            )
    for name, expected in spec.types.items():
        if name in state and state[name] is not None and value_type(state[name]) != expected:
            out.append(
                Violation(
                    "TV007",
                    name,
                    f"`{name}` must be of type {expected} after "
                    f"`{step.name}`, got {value_type(state[name])}.",
                    {},
                )
            )
    if spec.schema_ is not None and step.error is None:
        target = fields if fields is not None else step.output
        for problem in validate_schema(spec.schema_, target):
            out.append(
                Violation(
                    "TV006",
                    problem[0],
                    f"`{step.name}` output does not match its schema: "
                    f"{(problem[0] + ' ') if problem[0] else ''}{problem[1]}",
                    {"schema": spec.schema_ if isinstance(spec.schema_, str) else "inline"},
                )
            )
    return out


# ---------------------------------------------------------------- schemas

_schema_cache: dict[str, Any] = {}


def load_schema(ref: str) -> Any:
    """Import ``"pkg.module:Model"`` (or ``"pkg.module.Model"``) or load a ``.json`` schema file."""
    if ref in _schema_cache:
        return _schema_cache[ref]
    if ref.endswith(".json"):
        loaded: Any = json.loads(Path(ref).read_text(encoding="utf-8"))
    else:
        module_name, sep, attr = ref.partition(":")
        if not sep:
            module_name, _, attr = ref.rpartition(".")
        obj: Any = importlib.import_module(module_name)
        for part in attr.split("."):
            obj = getattr(obj, part)
        loaded = obj
    _schema_cache[ref] = loaded
    return loaded


def validate_schema(schema: str | dict[str, Any], value: Any) -> list[tuple[str | None, str]]:
    try:
        loaded = load_schema(schema) if isinstance(schema, str) else schema
    except Exception as exc:
        return [(None, f"could not load schema {schema!r}: {type(exc).__name__}: {exc}")]
    if isinstance(loaded, type) and hasattr(loaded, "model_validate"):
        try:
            loaded.model_validate(value)
        except Exception as exc:
            errors = getattr(exc, "errors", None)
            if callable(errors):
                items = errors()
                return [
                    (".".join(str(p) for p in e.get("loc", ())) or None, str(e.get("msg", "")))
                    for e in items[:10]
                ]
            return [(None, str(exc))]
        return []
    if isinstance(loaded, dict):
        return [(path or None, msg) for path, msg in json_schema_errors(loaded, value, "")]
    return [(None, f"schema {schema!r} is neither a pydantic model nor a JSON schema")]


_JSON_TYPES = {
    "object": "dict",
    "array": "list",
    "string": "str",
    "number": "number",
    "integer": "number",
    "boolean": "bool",
    "null": "null",
}


def json_schema_errors(schema: dict[str, Any], value: Any, path: str) -> list[tuple[str, str]]:
    """Validate the commonly used JSON Schema subset without extra dependencies."""
    errors: list[tuple[str, str]] = []
    expected = schema.get("type")
    actual = value_type(value)
    if expected is not None:
        allowed = [expected] if isinstance(expected, str) else list(expected)
        mapped = {_JSON_TYPES.get(t, t) for t in allowed}
        if actual not in mapped:
            return [(path, f"expected {'/'.join(allowed)}, got {actual}")]
        if "integer" in allowed and isinstance(value, float) and not value.is_integer():
            return [(path, "expected integer")]
    if "enum" in schema and value not in schema["enum"]:
        errors.append((path, f"value {value!r} not in {schema['enum']}"))
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append((f"{path}.{name}".lstrip("."), "is required"))
        for name, sub in schema.get("properties", {}).items():
            if name in value and isinstance(sub, dict):
                errors.extend(json_schema_errors(sub, value[name], f"{path}.{name}".lstrip(".")))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append((path, f"needs at least {schema['minItems']} items, got {len(value)}"))
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value[:50]):
                errors.extend(json_schema_errors(schema["items"], item, f"{path}[{index}]"))
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append((path, f"needs at least {schema['minLength']} characters"))
    return errors


# ---------------------------------------------------------------- inference


def infer(runs: list[RunView]) -> dict[str, Contract]:
    """Propose contracts from successful runs: fields always read, written and non-empty."""
    per_step: dict[str, dict[str, Any]] = {}
    for run in runs:
        seen_in_run: set[str] = set()
        for step in run.steps:
            if step.error is not None or step.name in seen_in_run:
                continue
            seen_in_run.add(step.name)
            fields = produced(step) or {}
            info = per_step.setdefault(
                step.name, {"count": 0, "writes": None, "non_empty": None, "reads": None, "types": {}}
            )
            info["count"] += 1
            writes = set(fields)
            non_empty = {k for k, v in fields.items() if not is_empty(v)}
            reads = set(step.reads)
            info["writes"] = writes if info["writes"] is None else info["writes"] & writes
            info["non_empty"] = non_empty if info["non_empty"] is None else info["non_empty"] & non_empty
            info["reads"] = reads if info["reads"] is None else info["reads"] & reads
            for key, value in fields.items():
                if value is not None:
                    info["types"].setdefault(key, set()).add(value_type(value))
    contracts: dict[str, Contract] = {}
    for name, info in per_step.items():
        types = {
            k: next(iter(v))
            for k, v in info["types"].items()
            if len(v) == 1 and k in (info["writes"] or set())
        }
        spec = Contract(
            step=name,
            reads=sorted(info["reads"] or []),
            writes=sorted(info["writes"] or []),
            non_empty=sorted(info["non_empty"] or []),
            types=types,
        )
        if spec.reads or spec.writes or spec.non_empty:
            contracts[name] = spec
    return contracts


def to_toml(contracts: dict[str, Contract]) -> str:
    def fmt(values: list[str]) -> str:
        return "[" + ", ".join(json.dumps(v) for v in values) + "]"

    lines = ["# Suggested by `tervic contracts infer`. Review before committing.", ""]
    for name, spec in sorted(contracts.items()):
        lines.append(f"[contracts.{json.dumps(name) if not name.isidentifier() else name}]")
        if spec.reads:
            lines.append(f"reads = {fmt(spec.reads)}")
        if spec.writes:
            lines.append(f"writes = {fmt(spec.writes)}")
        if spec.non_empty:
            lines.append(f"non_empty = {fmt(spec.non_empty)}")
        if spec.types:
            inner = ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(spec.types.items()))
            lines.append(f"types = {{ {inner} }}")
        lines.append("")
    return "\n".join(lines)
