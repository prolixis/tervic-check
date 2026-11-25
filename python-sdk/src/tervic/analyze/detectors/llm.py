"""7.3 LLM behaviour: TV201–TV205."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from tervic.analyze.contracts import json_schema_errors
from tervic.analyze.detectors._util import enabled, output_text
from tervic.analyze.findings import Context, Detector, Finding, preview, register
from tervic.core.events import LLMCall

_TRUNCATED = frozenset({"length", "max_tokens", "max_output_tokens", "model_length", "token_limit"})
_REFUSAL = re.compile(
    r"\b(?:I(?:'m| am)? (?:sorry,? )?(?:but )?(?:I )?(?:can(?:'|no)?t|cannot|won't|will not|am unable to|"
    r"'m unable to|'m not able to|am not able to) (?:help|assist|comply|provide|do that|fulfil?l|"
    r"answer|share|create|support)"
    r"|I must decline|I'm not comfortable|As an AI(?: language model)?, I (?:can(?:'|no)?t|cannot))",
    re.IGNORECASE,
)


def _finish(call: LLMCall) -> str:
    return (call.finish_reason or "").strip().lower()


def _owner_name(ctx: Context, call: LLMCall) -> str:
    owner = ctx.run.owner_step(call)
    return f"`{owner.name}`" if owner else "an LLM call"


@register
class TruncatedOutput(Detector):
    id = "TV201"
    name = "truncated-output"
    severity = "error"
    description = "The model stopped because it hit its token limit (finish_reason = length / max_tokens)."
    how_to_fix = "Raise max_tokens, shorten the prompt or ask for a shorter answer; treat a truncated answer as a failure."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for call in ctx.run.llm_calls:
            owner = ctx.run.owner_step(call)
            if _finish(call) in _TRUNCATED and enabled(ctx, self.id, owner):
                yield self.finding(
                    f"The LLM call in {_owner_name(ctx, call)} was cut off (finish_reason="
                    f"{call.finish_reason!r}, {call.output_tokens or '?'} output tokens).",
                    step=owner,
                    evidence={
                        "model": call.model,
                        "output_tail": preview(output_text(call.output)[-200:], 200),
                    },
                )


@register
class Refusal(Detector):
    id = "TV202"
    name = "refusal"
    severity = "error"
    description = "The model refused the request (provider refusal field or refusal wording)."
    how_to_fix = "Check the prompt and inputs that triggered the refusal; handle refusals explicitly instead of passing them on as answers."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for call in ctx.run.llm_calls:
            owner = ctx.run.owner_step(call)
            if not enabled(ctx, self.id, owner):
                continue
            if call.refusal or _finish(call) in ("content_filter", "refusal", "safety"):
                yield self.finding(
                    f"The model refused in {_owner_name(ctx, call)}: "
                    f"{preview(call.refusal or call.finish_reason, 120)}",
                    step=owner,
                    evidence={"model": call.model, "finish_reason": call.finish_reason},
                )
                continue
            text = output_text(call.output)
            match = _REFUSAL.search(text[:600])
            if match and len(text) < 1500:
                yield self.finding(
                    f"The model's reply in {_owner_name(ctx, call)} reads like a refusal: "
                    f"{preview(text[match.start() :], 100)}",
                    step=owner,
                    severity="warn",
                    evidence={"model": call.model},
                )


def _tool_names(call: LLMCall, ctx: Context) -> set[str] | None:
    if call.available_tools is not None:
        return set(call.available_tools)
    return None


@register
class HallucinatedTool(Detector):
    id = "TV203"
    name = "hallucinated-tool"
    severity = "error"
    description = "The model called a tool name that was not available to it."
    how_to_fix = "Check the tool list sent to the model and the tool names in the prompt; make the model choose only from real tools."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for call in ctx.run.llm_calls:
            available = _tool_names(call, ctx)
            owner = ctx.run.owner_step(call)
            if available is None or not enabled(ctx, self.id, owner):
                continue
            for requested in call.tool_calls:
                if requested.name not in available:
                    yield self.finding(
                        f"The model in {_owner_name(ctx, call)} called `{requested.name}`, which is not "
                        f"one of its tools ({', '.join(sorted(available)) or 'none'}).",
                        step=owner,
                        evidence={"tool": requested.name, "available": sorted(available)},
                    )
        for tool in ctx.run.tool_calls:
            owner = ctx.run.owner_step(tool)
            if (
                tool.available_tools is not None
                and tool.tool_name not in tool.available_tools
                and enabled(ctx, self.id, owner)
            ):
                yield self.finding(
                    f"Tool `{tool.tool_name}` was invoked but is not in the available tools "
                    f"({', '.join(sorted(tool.available_tools)) or 'none'}).",
                    step=owner,
                    evidence={"tool": tool.tool_name},
                )


@register
class InvalidToolArgs(Detector):
    id = "TV204"
    name = "invalid-tool-args"
    severity = "error"
    description = "Tool arguments were not valid JSON or failed the tool's schema."
    how_to_fix = (
        "Use strict tool schemas / structured outputs and validate arguments before calling the tool."
    )

    def run(self, ctx: Context) -> Iterator[Finding]:
        for call in ctx.run.llm_calls:
            owner = ctx.run.owner_step(call)
            if not enabled(ctx, self.id, owner):
                continue
            for requested in call.tool_calls:
                if isinstance(requested.args, str) and requested.args.strip():
                    try:
                        json.loads(requested.args)
                    except (json.JSONDecodeError, ValueError):
                        yield self.finding(
                            f"The model in {_owner_name(ctx, call)} sent invalid JSON arguments to "
                            f"`{requested.name}`: {preview(requested.args, 80)}",
                            step=owner,
                            evidence={"tool": requested.name},
                        )
        for tool in ctx.run.tool_calls:
            owner = ctx.run.owner_step(tool)
            schema = tool.tool_schema
            if not isinstance(schema, dict) or not enabled(ctx, self.id, owner):
                continue
            args: Any = tool.args
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    yield self.finding(
                        f"Tool `{tool.tool_name}` got arguments that are not valid JSON.",
                        step=owner,
                        evidence={"args": preview(tool.args, 120)},
                    )
                    continue
            problems = json_schema_errors(schema, args, "")
            if problems:
                where, why = problems[0]
                yield self.finding(
                    f"Tool `{tool.tool_name}` got arguments that fail its schema: {where or 'args'} {why}.",
                    step=owner,
                    field_path=where or None,
                    evidence={"args": tool.args, "problems": [f"{p} {m}" for p, m in problems[:5]]},
                )


@register
class EmptyCompletion(Detector):
    id = "TV205"
    name = "empty-completion"
    severity = "error"
    description = "The model returned no text and no tool calls."
    how_to_fix = "Check for content filtering, an empty prompt or a malformed request; treat an empty completion as a failure."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for call in ctx.run.llm_calls:
            owner = ctx.run.owner_step(call)
            if not enabled(ctx, self.id, owner) or call.tool_calls or call.refusal:
                continue
            if _finish(call) in _TRUNCATED:
                continue
            if output_text(call.output).strip() == "":
                yield self.finding(
                    f"The LLM call in {_owner_name(ctx, call)} returned no text and no tool calls.",
                    step=owner,
                    evidence={"model": call.model, "finish_reason": call.finish_reason},
                )
