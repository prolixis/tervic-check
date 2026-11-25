"""7.2 Swallowed errors: TV101–TV104."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from tervic.analyze.detectors._util import enabled, output_text
from tervic.analyze.findings import Context, Detector, Finding, preview, register
from tervic.analyze.model import StepView
from tervic.core.events import ToolCall
from tervic.core.serialize import MARK, is_empty

_ERROR_TEXT = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*(?:[A-Z][A-Za-z]*(?:Error|Exception)|Error|Exception)\s*:\s*\S"
    r"|\b(?:4\d\d|5\d\d)\s+(?:Bad Request|Unauthorized|Forbidden|Not Found|Method Not Allowed|"
    r"Conflict|Unprocessable Entity|Too Many Requests|Internal Server Error|Bad Gateway|"
    r"Service Unavailable|Gateway Timeout)\b"
    r"|\"status(?:_code|Code)?\"\s*:\s*[45]\d\d\b"
    r"|<title>\s*(?:4\d\d|5\d\d)\b"
    # explicit error statements that agent frameworks put into tool observations
    r"|\b(?i:I )?(?i:encountered an error)\b|(?i:\bThis was the error:)"
    r"|(?i:\berror (?:occurred )?while (?:trying|calling|using)\b)",
    re.MULTILINE,
)
_ERROR_KEYS = ("error", "errors", "error_message", "errorMessage", "exception", "err")
_RETRY_TEXT = re.compile(
    r"max(?:imum)?[ _-]?retries|retries exhausted|retry limit|RetryError|gave up after \d+", re.IGNORECASE
)


def error_in(value: Any, depth: int = 0) -> str | None:
    """Return a short description of error content in a snapshot, or None."""
    if depth > 3:
        return None
    if isinstance(value, dict) and MARK not in value:
        for key in _ERROR_KEYS:
            if key in value and not is_empty(value[key]) and value[key] is not False:
                return f'{{"{key}": {preview(value[key], 80)}}}'
        if value.get("ok") is False or value.get("success") is False:
            return f"{preview(value, 80)} (ok/success is false)"
        for item in value.values():
            found = error_in(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value[:50]:
            found = error_in(item, depth + 1)
            if found:
                return found
        return None
    text = output_text(value) if not isinstance(value, str) else value
    if text and text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict | list):
            found = error_in(parsed, depth + 1)
            if found:
                return found
    match = _ERROR_TEXT.search(text or "")
    if match:
        return preview(text[max(0, match.start() - 20) : match.end() + 60], 120)
    return None


def tool_failed(call: ToolCall) -> str | None:
    if call.error is not None:
        return f"{call.error.type}: {call.error.message}"
    return error_in(call.result)


@register
class ErrorInOutput(Detector):
    id = "TV101"
    name = "error-in-output"
    severity = "error"
    description = (
        "A step's output contains error text (Traceback, `Error:`, HTTP 4xx/5xx bodies, "
        '`{"error": ...}`) but no exception was raised.'
    )
    how_to_fix = "Raise the error, or return an explicit error state the next step must handle; do not pass error text along as data."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if step.error is not None or not step.ended or not enabled(ctx, self.id, step):
                continue
            found = error_in(step.output)
            if found:
                yield self.finding(
                    f"`{step.name}` returned error content but raised no exception: {found}",
                    step=step,
                    evidence={"error_excerpt": found},
                )


@register
class ToolErrorIgnored(Detector):
    id = "TV102"
    name = "tool-error-ignored"
    severity = "error"
    description = "A tool returned an error and the agent continued as if it had succeeded."
    how_to_fix = (
        "Check the tool result before using it: retry, surface the error to the user, or stop the run."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        calls = ctx.run.tool_calls
        for index, call in enumerate(calls):
            failure = tool_failed(call)
            if failure is None:
                continue
            owner = ctx.run.owner_step(call)
            if not enabled(ctx, self.id, owner):
                continue
            retried_ok = any(
                later.tool_name == call.tool_name and tool_failed(later) is None
                for later in calls[index + 1 :]
            )
            if retried_ok:
                continue
            continued = any(e.ts > call.ts for e in ctx.run.llm_calls) or any(
                s.started_ts > call.ts and (owner is None or s.step_id != owner.step_id)
                for s in ctx.run.steps
            )
            if not continued and ctx.run.status != "completed":
                continue
            if owner is not None and owner.error is not None:
                continue
            yield self.finding(
                f"Tool `{call.tool_name}` failed ({failure}) and the run continued as if it succeeded.",
                step=owner,
                evidence={"tool": call.tool_name, "args": call.args, "result": call.result},
            )


@register
class CaughtParseFailure(Detector):
    id = "TV103"
    name = "caught-parse-failure"
    severity = "error"
    description = "JSON or structured-output parsing failed and the code fell back to a default value."
    how_to_fix = "Use the provider's structured output mode or validate and retry; never replace a failed parse with an empty default silently."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for step in ctx.run.steps:
            if step.error is not None or not step.ended or not enabled(ctx, self.id, step):
                continue
            bad = _malformed_json_input(step)
            if bad is not None and _looks_default(step.output):
                yield self.finding(
                    f"`{step.name}` received malformed JSON and returned a default "
                    f"({preview(step.output, 40)}) instead of failing.",
                    step=step,
                    evidence={"malformed": bad, "output": step.output},
                )
                continue
            for call in step.llm_calls:
                text = output_text(call.output).strip()
                if not _wants_json(text):
                    continue
                if _parses(text):
                    continue
                if _looks_default(step.output) or any(is_empty(v) for v in step.writes.values()):
                    yield self.finding(
                        f"The LLM call in `{step.name}` returned invalid JSON and the step fell back to "
                        f"a default ({preview(step.output, 40)}).",
                        step=step,
                        evidence={"llm_output": preview(text, 200), "output": step.output},
                    )
                    break


def _wants_json(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(("{", "[", "```json", "```\n{"))


def _parses(text: str) -> bool:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        candidate = candidate.rsplit("```", 1)[0]
    try:
        json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _malformed_json_input(step: StepView) -> str | None:
    values = step.input.values() if isinstance(step.input, dict) else [step.input]
    for value in values:
        if isinstance(value, str) and _wants_json(value) and not _parses(value):
            return preview(value, 120)
    return None


def _looks_default(value: Any) -> bool:
    if is_empty(value):
        return True
    if isinstance(value, dict) and MARK not in value:
        return all(is_empty(v) or v in (0, False) for v in value.values())
    return False


@register
class RetryExhaustedSilent(Detector):
    id = "TV104"
    name = "retry-exhausted-silent"
    severity = "error"
    description = "A step or tool hit its retry limit, still failed, and the run carried on."
    how_to_fix = (
        "When retries are exhausted, raise or route to an explicit failure path instead of continuing."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        reported: set[str] = set()
        steps = ctx.run.steps
        for index, step in enumerate(steps):
            if step.attempt < 2 or step.error is None or step.name in reported:
                continue
            nxt = steps[index + 1] if index + 1 < len(steps) else None
            if nxt is not None and nxt.name == step.name:
                continue  # still retrying
            continued = nxt is not None or ctx.run.status == "completed"
            if continued and enabled(ctx, self.id, step):
                reported.add(step.name)
                yield self.finding(
                    f"`{step.name}` failed {step.attempt} times in a row ({step.error.type}: "
                    f"{step.error.message}) and the run continued.",
                    step=step,
                    evidence={"attempts": step.attempt},
                )
        tool_streak: dict[str, int] = {}
        for call in ctx.run.tool_calls:
            if tool_failed(call):
                tool_streak[call.tool_name] = tool_streak.get(call.tool_name, 0) + 1
                continue
            tool_streak[call.tool_name] = 0
        for call in reversed(ctx.run.tool_calls):
            streak = tool_streak.get(call.tool_name, 0)
            if streak >= 3 and call.tool_name not in reported and ctx.run.status == "completed":
                owner = ctx.run.owner_step(call)
                if enabled(ctx, self.id, owner):
                    reported.add(call.tool_name)
                    yield self.finding(
                        f"Tool `{call.tool_name}` failed {streak} times in a row and the run still completed.",
                        step=owner,
                        evidence={"failures": streak},
                    )
        for step in steps:
            if step.error is not None or step.name in reported or not enabled(ctx, self.id, step):
                continue
            text = output_text(step.output)
            match = _RETRY_TEXT.search(text)
            if match:
                reported.add(step.name)
                yield self.finding(
                    f"`{step.name}` returned '{match.group(0)}' as data and the run continued.",
                    step=step,
                    evidence={"excerpt": preview(text[max(0, match.start() - 40) :], 120)},
                )
