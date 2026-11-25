"""Baseline regressions: TV701 (fields lost, values emptied, steps skipped, cost/latency/tokens worse)."""

from __future__ import annotations

from collections.abc import Iterator

from tervic.analyze.baseline import diff_runs
from tervic.analyze.detectors._util import enabled
from tervic.analyze.findings import Context, Detector, Finding, register


@register
class BaselineRegression(Detector):
    id = "TV701"
    name = "baseline-regression"
    severity = "warn"
    description = (
        "Compared with the committed baseline for this run name: a step lost a field, a value became "
        "empty, a step was skipped, or cost/latency/tokens got more than 50% worse (configurable)."
    )
    how_to_fix = "Run `tervic diff <baseline-run> <run>` to see every change, fix the regression, or re-set the baseline if the change is intended."

    def run(self, ctx: Context) -> Iterator[Finding]:
        if ctx.baseline is None or ctx.baseline.run_id == ctx.run.run_id:
            return
        diff = diff_runs(ctx.baseline, ctx.run, ctx.config.detectors.baseline_regression_pct)
        ctx.extra["baseline_diff"] = diff
        steps = {s.name: s for s in ctx.run.steps}
        for name in diff.steps_removed:
            if enabled(ctx, self.id, None):
                yield self.finding(
                    f"Step `{name}` ran in the baseline '{ctx.baseline_name}' but not in this run.",
                    evidence={"baseline": ctx.baseline_name, "step": name},
                )
        for change in diff.changes:
            step = steps.get(change.step)
            if change.change not in ("removed", "became_empty") or not enabled(ctx, self.id, step):
                continue
            what = f"`{change.field}`" if change.field else "its output"
            verb = "no longer writes" if change.change == "removed" else "now leaves empty"
            yield self.finding(
                f"`{change.step}` {verb} {what} (baseline '{ctx.baseline_name}' had {change.before}).",
                step=step,
                field_path=change.field,
                evidence={"baseline": ctx.baseline_name, "before": change.before, "after": change.after},
            )
        for metric in diff.metrics:
            if metric.regression and enabled(ctx, self.id, None):
                yield self.finding(
                    f"{metric.metric} went from {metric.before:.4g} to {metric.after:.4g} "
                    f"(+{metric.pct:.0f}%) compared with baseline '{ctx.baseline_name}'.",
                    evidence=metric.model_dump(),
                )
