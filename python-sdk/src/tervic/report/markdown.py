"""Markdown report, used as a pull-request comment (one comment, updated in place via the marker)."""

from __future__ import annotations

from tervic.analyze.engine import Report

MARKER = "<!-- tervic-report -->"
ICON = {"pass": "🟢", "warn": "🟡", "fail": "🔴"}
SEV = {"error": "❌", "warn": "⚠️", "info": "ℹ️"}


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(reports: list[Report], *, title: str = "Tervic") -> str:
    lines = [MARKER, f"## {title}", ""]
    if not reports:
        lines.append("No agent runs were recorded.")
        return "\n".join(lines) + "\n"
    worst = (
        "fail"
        if any(r.verdict == "fail" for r in reports)
        else ("warn" if any(r.verdict == "warn" for r in reports) else "pass")
    )
    lines.append(f"**{ICON[worst]} {worst.upper()}** · {len(reports)} run(s) checked")
    lines.append("")
    lines.append("| Run | Verdict | Steps | Cost | Errors | Warnings |")
    lines.append("|---|---|---|---|---|---|")
    for report in reports:
        lines.append(
            f"| `{_escape(report.run_name)}` `{report.run_id[:10]}` | "
            f"{ICON[report.verdict]} {report.verdict} | "
            f"{report.stats.steps} | ${report.stats.cost_usd:.4f} | {report.counts.get('error', 0)} | "
            f"{report.counts.get('warn', 0)} |"
        )
    for report in reports:
        if not report.groups:
            continue
        lines += ["", f"### `{_escape(report.run_name)}` — root causes", ""]
        for group in report.groups:
            root = f"`{group.root_cause_step_name}`" if group.root_cause_step_name else "run"
            where = f" (`{group.source}`)" if group.source else ""
            lines.append(
                f"- {SEV[group.severity]} **{root}**{where}: {_escape(group.title)} "
                f"<sub>{', '.join(group.detector_ids)}</sub>"
            )
            symptoms = group.findings[1:]
            if symptoms:
                lines.append("  <details><summary>" + f"{len(symptoms)} related finding(s)</summary>\n")
                for finding in symptoms:
                    lines.append(f"  - `{finding.detector_id}` {_escape(finding.message)}")
                lines.append("  </details>")
        lines.append("")
        lines.append(
            f"Fix prompt: `tervic fix {report.run_id}` · Replay: "
            f"`tervic replay {report.run_id} --from <step>`"
        )
    return "\n".join(lines).rstrip() + "\n"
