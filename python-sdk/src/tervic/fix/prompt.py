"""``tervic fix``: a ready-to-paste prompt for Claude Code, Cursor or any coding agent.

Generated offline from the report and the recorded run: no LLM is involved.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any, Literal

from tervic.analyze.engine import Report
from tervic.analyze.findings import Finding, preview
from tervic.analyze.lineage import RootCauseGroup
from tervic.analyze.model import RunView, StepView
from tervic.core.serialize import MARK, is_empty, item_count

Format = Literal["plain", "claude", "cursor"]

_WHY: dict[str, str] = {
    "TV001": "`{step}` returns an empty result",
    "TV002": "`{field}` is dropped from the state",
    "TV003": "`{field}` can be empty",
    "TV004": "`{field}` is never written before it is read",
    "TV005": "`{field}` changes type",
    "TV006": "the output does not match its schema",
    "TV007": "the step breaks its contract for `{field}`",
    "TV101": "error content is returned as data instead of raised",
    "TV102": "the tool error is ignored",
    "TV103": "a parse failure is replaced by a default",
    "TV104": "retries are exhausted but the run continues",
    "TV201": "the model output is truncated",
    "TV202": "the model refuses",
    "TV203": "the model calls a tool that does not exist",
    "TV204": "the tool arguments are invalid",
    "TV205": "the model returns an empty completion",
    "TV301": "`{field}` can be empty",
    "TV302": "the retrieved documents are not used",
    "TV401": "it repeats without making progress",
    "TV402": "the run ends without an answer",
    "TV403": "the run costs more than the budget",
    "TV404": "it is slower than the budget",
    "TV405": "the run uses more tokens than the budget",
    "TV501": "a secret reaches the model or the output",
    "TV502": "personal data reaches the output",
    "TV701": "it regressed compared with the baseline",
}

_EMPTY_FAMILY = {"TV001", "TV003", "TV301", "TV302", "TV402"}


def _describe(value: Any) -> str:
    count = item_count(value)
    if count is not None:
        return f"{preview(value, 80)} ({count} items)"
    return preview(value, 100)


def _field_value(step: StepView, field: str | None) -> Any:
    if field and isinstance(step.output, dict) and MARK not in step.output and field in step.output:
        return step.output[field]
    return step.output


def _evidence(group: RootCauseGroup, run: RunView, baseline: RunView | None) -> str:
    root = run.step(group.root_cause_step_id)
    if root is None:
        primary = group.primary
        return "; ".join(f"{k}={preview(v, 60)}" for k, v in list(primary.evidence.items())[:3]) or "-"
    field = next((f.field_path for f in group.findings if f.field_path), None)
    parts: list[str] = []
    if isinstance(root.input, dict) and MARK not in root.input:
        shown = [f"{k}={preview(v, 60)}" for k, v in list(root.input.items())[:2]]
        if shown:
            parts.append("input " + ", ".join(shown))
    elif root.input is not None:
        parts.append(f"input {preview(root.input, 80)}")
    out = _field_value(root, field)
    if root.error is not None:
        parts.append(f"error {root.error.type}: {root.error.message[:120]}")
    elif field:
        parts.append(f"output {field}={_describe(out)}")
    else:
        parts.append(f"output {_describe(out)}")
    text = ", ".join(parts)
    if baseline is not None:
        base = next((s for s in baseline.steps if s.name == root.name), None)
        if base is not None:
            base_val = _field_value(base, field)
            count = item_count(base_val)
            what = f"{count} {field or 'items'}" if count is not None else _describe(base_val)
            text += f". Baseline run had {what}."
    return text + ("" if text.endswith(".") else ".")


def _root_line(group: RootCauseGroup, run: RunView) -> str:
    primary = group.primary
    root = run.step(group.root_cause_step_id)
    if root is None:
        return f"Root cause: {primary.message}"
    where = f" ({group.source})" if group.source else ""
    at_root = [f for f in group.findings if f.step_id == root.step_id]
    message = (at_root[0] if at_root else primary).message
    return f"Root cause: step `{root.name}`{where}: {message}"


def _effect_line(group: RootCauseGroup) -> str:
    ids = ", ".join(group.detector_ids)
    elsewhere = [f for f in group.findings if f.step_id != group.root_cause_step_id]
    if elsewhere:
        steps = list(dict.fromkeys(f"`{f.step_name}`" for f in elsewhere if f.step_name))
        text = f"{', '.join(steps) or 'later steps'} ran on the bad value: {elsewhere[0].message}"
    else:
        others = [f.message for f in group.findings[1:]]
        text = " ".join(others) if others else group.primary.message
    return f"Effect: {text.rstrip('.')} ({ids})."


def _steps(group: RootCauseGroup, run: RunView, run_id: str) -> list[str]:
    primary: Finding = group.primary
    root = run.step(group.root_cause_step_id)
    field = next((f.field_path for f in group.findings if f.field_path), None)
    why = _WHY.get(primary.detector_id, "it produces this output").format(
        step=root.name if root else "the step", field=field or "the value"
    )
    location = group.source or (f"step `{root.name}`" if root else "the code that produced this")
    lines = [f"Open {location} and find why {why}."]
    lines.append("Fix the cause (do not just add a fallback that hides it).")
    if primary.detector_id in _EMPTY_FAMILY or (
        field and is_empty(_field_value(root, field) if root else None)
    ):
        lines.append(
            'If an empty result is legitimate, raise or return an explicit "no results" state the '
            "next step handles."
        )
    elif primary.how_to_fix:
        lines.append(primary.how_to_fix)
    if root is not None:
        lines.append(f"Re-run with: tervic replay {run_id} --from {root.name}")
    else:
        lines.append("Re-run your agent and check with: tervic check")
    return lines


def build_prompt(report: Report, run: RunView, baseline: RunView | None = None, fmt: Format = "plain") -> str:
    if not report.groups:
        return f"Tervic found no problems in run {report.run_id} ({report.run_name}). Nothing to fix."
    groups = [g for g in report.groups if g.severity in ("error", "warn")] or report.groups
    header = f"Tervic found a silent failure in run {report.run_id} ({report.run_name})."
    if len(groups) > 1:
        header = f"Tervic found {len(groups)} problems in run {report.run_id} ({report.run_name})."
    blocks: list[str] = []
    for index, group in enumerate(groups, 1):
        lines = []
        if len(groups) > 1:
            lines.append(f"## Problem {index}")
        lines.append(_root_line(group, run))
        lines.append(_effect_line(group))
        lines.append(f"Evidence: {_evidence(group, run, baseline)}")
        lines.append("")
        lines.append("Please:")
        lines.extend(f"{n}. {text}" for n, text in enumerate(_steps(group, run, report.run_id), 1))
        blocks.append("\n".join(lines))
    body = header + "\n\n" + "\n\n".join(blocks)
    if fmt == "claude":
        body += (
            "\n\nIf the Tervic MCP server is connected, call get_findings and get_step for more detail, "
            "and run_replay to verify the fix."
        )
    elif fmt == "cursor":
        for group in groups:
            if group.source:
                path = group.source.rsplit(":", 1)[0]
                body = body.replace(
                    f"Open {group.source}", f"Open @{path} (line {group.source.rsplit(':', 1)[-1]})"
                )
    return body + "\n"


def copy_to_clipboard(text: str) -> bool:
    """Best effort; returns False if no clipboard tool is available."""
    candidates: list[list[str]] = []
    if sys.platform == "win32":
        candidates.append(["clip"])
    elif sys.platform == "darwin":
        candidates.append(["pbcopy"])
    else:
        candidates += [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            payload = ("﻿" + text).encode("utf-16le") if cmd[0] == "clip" else text.encode("utf-8")
            subprocess.run(cmd, input=payload, check=True, timeout=5, capture_output=True)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False
