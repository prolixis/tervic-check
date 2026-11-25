"""Finding and report models, plus the detector base class."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tervic.analyze.model import RunView, StepView
    from tervic.config import TervicConfig

Severity = Literal["error", "warn", "info"]
SEVERITY_RANK: dict[str, int] = {"error": 3, "warn": 2, "info": 1}


class Finding(BaseModel):
    detector_id: str
    name: str = ""
    severity: Severity = "error"
    step_id: str | None = None
    step_name: str | None = None
    field_path: str | None = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    root_cause_step_id: str | None = None
    root_cause_step_name: str | None = None
    source: str | None = None
    how_to_fix: str = ""


@dataclass
class Context:
    """Everything a detector may look at."""

    run: RunView
    config: TervicConfig
    baseline: RunView | None = None
    baseline_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Detector:
    id: ClassVar[str]
    name: ClassVar[str]
    severity: ClassVar[Severity] = "error"
    description: ClassVar[str] = ""
    how_to_fix: ClassVar[str] = ""

    def run(self, ctx: Context) -> Iterable[Finding]:
        raise NotImplementedError

    def finding(
        self,
        message: str,
        *,
        step: StepView | None = None,
        field_path: str | None = None,
        evidence: dict[str, Any] | None = None,
        severity: Severity | None = None,
    ) -> Finding:
        return Finding(
            detector_id=self.id,
            name=self.name,
            severity=severity or self.severity,
            step_id=step.step_id if step else None,
            step_name=step.name if step else None,
            field_path=field_path,
            message=message,
            evidence=evidence or {},
            source=step.source if step else None,
            how_to_fix=self.how_to_fix,
        )


REGISTRY: list[type[Detector]] = []


def register(cls: type[Detector]) -> type[Detector]:
    REGISTRY.append(cls)
    return cls


def preview(value: Any, limit: int = 160) -> str:
    """Short, single-line preview of a snapshot for evidence and messages."""
    from tervic.core.serialize import MARK, is_marker

    if is_marker(value, "str"):
        text = f"<{value.get('len')} chars> " + str(value.get("head", ""))[:limit]
    elif isinstance(value, str):
        text = repr(value)
    elif isinstance(value, dict) and MARK in value:
        text = f"<{value[MARK]}>"
    else:
        import json

        try:
            text = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
