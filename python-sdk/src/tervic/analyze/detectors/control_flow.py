"""7.5 Control flow & budgets: TV401–TV405."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from typing import Any

from tervic.analyze.detectors._util import enabled
from tervic.analyze.findings import Context, Detector, Finding, preview, register
from tervic.core.serialize import is_empty


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@register
class Loop(Detector):
    id = "TV401"
    name = "loop"
    severity = "error"
    description = "The same step ran with the same input, or the same tool was called with the same arguments, more than N times (default 5)."
    how_to_fix = "Add a stop condition or a max-iterations guard, and make the agent notice when a tool keeps returning the same result."

    def run(self, ctx: Context) -> Iterator[Finding]:
        limit = ctx.config.detectors.loop_threshold
        steps = Counter((s.name, _fingerprint(s.input)) for s in ctx.run.steps if s.kind != "llm")
        for (name, _), count in steps.items():
            if count <= limit:
                continue
            step = next(s for s in ctx.run.steps if s.name == name)
            if enabled(ctx, self.id, step):
                yield self.finding(
                    f"`{name}` ran {count} times with the same input (limit {limit}).",
                    step=step,
                    evidence={"count": count, "limit": limit, "input": step.input},
                )
        tools = Counter((c.tool_name, _fingerprint(c.args)) for c in ctx.run.tool_calls)
        for (tool, _), count in tools.items():
            if count <= limit:
                continue
            call = next(c for c in ctx.run.tool_calls if c.tool_name == tool)
            owner = ctx.run.owner_step(call)
            if enabled(ctx, self.id, owner):
                yield self.finding(
                    f"Tool `{tool}` was called {count} times with the same arguments "
                    f"({preview(call.args, 60)}; limit {limit}).",
                    step=owner,
                    evidence={"tool": tool, "count": count, "limit": limit, "args": call.args},
                )


@register
class DeadEnd(Detector):
    id = "TV402"
    name = "dead-end"
    severity = "error"
    description = "The run finished without raising, but produced no final output."
    how_to_fix = "Make sure every path through the agent ends with an answer, or raises."

    def run(self, ctx: Context) -> Iterator[Finding]:
        if ctx.run.status != "completed" or not enabled(ctx, self.id, None):
            return
        final = ctx.run.effective_final_output
        if not ctx.run.steps and not ctx.run.final_output_explicit:
            return
        if is_empty(final):
            last = ctx.run.steps[-1] if ctx.run.steps else None
            yield self.finding(
                "The run completed but produced no final output.",
                step=last,
                evidence={"final_output": final},
            )


class _Budget(Detector):
    severity = "error"


@register
class CostBudget(_Budget):
    id = "TV403"
    name = "cost-budget"
    description = "Total run cost is over budget.cost_usd."
    how_to_fix = "Use a cheaper model for simple steps, cut prompt size, or cache repeated calls."

    def run(self, ctx: Context) -> Iterator[Finding]:
        limit = ctx.config.budget.cost_usd
        cost = ctx.run.total_cost
        if limit is not None and cost > limit and enabled(ctx, self.id, None):
            yield self.finding(
                f"The run cost ${cost:.4f}, over the ${limit:.4f} budget.",
                evidence={"cost_usd": cost, "budget_usd": limit},
            )


@register
class LatencyBudget(_Budget):
    id = "TV404"
    name = "latency-budget"
    description = "The run, or a single step, took longer than budget.latency_ms (budget.step_latency_ms for steps, if set)."
    how_to_fix = "Find the slow step in `tervic show`, then parallelize, cache or use a faster model."

    def run(self, ctx: Context) -> Iterator[Finding]:
        limit = ctx.config.budget.latency_ms
        if limit is not None and ctx.run.duration_ms > limit and enabled(ctx, self.id, None):
            yield self.finding(
                f"The run took {ctx.run.duration_ms:.0f} ms, over the {limit:.0f} ms budget.",
                evidence={"duration_ms": ctx.run.duration_ms, "budget_ms": limit},
            )
        step_limit = ctx.config.budget.step_latency_ms or limit
        if step_limit is None:
            return
        for step in ctx.run.root_steps if ctx.config.budget.step_latency_ms is None else ctx.run.steps:
            if step.duration_ms > step_limit and enabled(ctx, self.id, step):
                yield self.finding(
                    f"`{step.name}` took {step.duration_ms:.0f} ms, over the {step_limit:.0f} ms step budget.",
                    step=step,
                    evidence={"duration_ms": step.duration_ms, "budget_ms": step_limit},
                )


@register
class TokenBudget(_Budget):
    id = "TV405"
    name = "token-budget"
    description = "Total tokens used by the run are over budget.tokens."
    how_to_fix = "Trim context (history, retrieved documents) and cap max_tokens per call."

    def run(self, ctx: Context) -> Iterator[Finding]:
        limit = ctx.config.budget.tokens
        tokens = ctx.run.tokens
        if limit is not None and tokens > limit and enabled(ctx, self.id, None):
            yield self.finding(
                f"The run used {tokens} tokens, over the {limit} token budget.",
                evidence={"tokens": tokens, "budget": limit},
            )
