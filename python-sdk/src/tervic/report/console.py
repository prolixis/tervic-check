"""Rich terminal output (honours ``NO_COLOR``)."""

from __future__ import annotations

from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from tervic.analyze.baseline import RunDiff
from tervic.analyze.engine import Report
from tervic.store.local import RunRow

VERDICT_STYLE = {"pass": "bold green", "warn": "bold yellow", "fail": "bold red"}
SEVERITY_STYLE = {"error": "red", "warn": "yellow", "info": "cyan"}
SEVERITY_ICON = {"error": "✗", "warn": "!", "info": "i"}


def make_console(no_color: bool = False) -> Console:
    return Console(no_color=no_color or None, highlight=False, soft_wrap=False)


def verdict_text(verdict: str | None) -> Text:
    value = (verdict or "?").upper()
    return Text(value, style=VERDICT_STYLE.get(verdict or "", "bold"))


def _ms(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1000:.2f}s" if value >= 1000 else f"{value:.0f}ms"


def _cost(value: float | None) -> str:
    return f"${value:.4f}" if value else "-"


def runs_table(rows: list[RunRow]) -> Table:
    table = Table(title="Recent runs", show_lines=False, expand=False)
    for column in ("run id", "verdict", "name", "started", "duration", "cost", "findings", "git"):
        table.add_column(column, no_wrap=column in ("run id", "verdict", "git"))
    for row in rows:
        findings = f"{row.errors}E {row.warnings}W" if (row.errors or row.warnings) else "-"
        table.add_row(
            row.run_id,
            verdict_text(row.verdict),
            row.name,
            (row.started_at or "")[:19].replace("T", " "),
            _ms(row.duration_ms),
            _cost(row.cost_usd),
            findings,
            (row.git_sha or "")[:8],
        )
    return table


def header(report: Report) -> Text:
    text = Text()
    text.append("Tervic ", style="bold")
    text.append_text(verdict_text(report.verdict))
    text.append(f"  {report.run_name}  ", style="bold")
    text.append(f"run {report.run_id}", style="dim")
    stats = report.stats
    text.append(
        f"\n{stats.steps} steps · {stats.llm_calls} LLM calls · {stats.tool_calls} tool calls · "
        f"{_ms(stats.duration_ms)} · {_cost(stats.cost_usd) if stats.cost_usd else '$0'}",
        style="dim",
    )
    if report.status == "crashed" and report.run_error:
        text.append(f"\nRun crashed: {report.run_error}", style="red")
    if report.baseline:
        text.append(f"\nCompared with baseline '{report.baseline}'", style="dim")
    return text


def findings_view(report: Report) -> Group | Text:
    if not report.groups:
        return Text("No silent failures found.", style="green")
    parts: list[Any] = []
    for index, group in enumerate(report.groups, 1):
        style = SEVERITY_STYLE[group.severity]
        title = Text()
        title.append(f"{index}. ", style="bold")
        title.append(f"{SEVERITY_ICON[group.severity]} ", style=style)
        root = group.root_cause_step_name
        title.append(f"Root cause: {root}" if root else "Run-level", style=f"bold {style}")
        if group.source:
            title.append(f"  ({group.source})", style="dim")
        body = Tree(title)
        for finding in group.findings:
            line = Text()
            line.append(f"{finding.detector_id} {finding.name}", style=SEVERITY_STYLE[finding.severity])
            if finding.step_name and finding.step_name != root:
                line.append(f" @ {finding.step_name}", style="dim")
            line.append(f": {finding.message}")
            body.add(line)
        hint = group.primary.how_to_fix
        if hint:
            body.add(Text(f"fix: {hint}", style="dim italic"))
        parts.append(body)
    return Group(*parts)


def step_tree(report: Report) -> Tree:
    findings_by_step: dict[str, list[Any]] = {}
    for finding in report.findings:
        if finding.step_id:
            findings_by_step.setdefault(finding.step_id, []).append(finding)
    tree = Tree(Text(f"{report.run_name} ({_ms(report.stats.duration_ms)})", style="bold"))
    nodes: dict[str, Tree] = {}
    for step in report.steps:
        label = Text()
        icon_style = SEVERITY_STYLE.get(step.severity or "", "green")
        label.append("● ", style=icon_style if step.severity else "green")
        label.append(step.name, style="bold")
        label.append(f"  {step.kind} · {_ms(step.duration_ms)}", style="dim")
        if step.cost_usd:
            label.append(f" · {_cost(step.cost_usd)}", style="dim")
        if step.error:
            label.append(f"  {step.error}", style="red")
        parent = nodes.get(step.parent_step_id or "", tree)
        node = parent.add(label)
        nodes[step.step_id] = node
        for finding in findings_by_step.get(step.step_id, []):
            node.add(
                Text(f"{finding.detector_id}: {finding.message}", style=SEVERITY_STYLE[finding.severity])
            )
    return tree


def print_report(console: Console, report: Report, *, tree: bool = False) -> None:
    console.print(header(report))
    console.print()
    if tree:
        console.print(step_tree(report))
        console.print()
    console.print(findings_view(report))


def diff_view(diff: RunDiff) -> Panel:
    lines = Text()
    lines.append(f"{diff.a}  →  {diff.b}\n", style="bold")
    if diff.steps_removed:
        lines.append(f"steps removed: {', '.join(diff.steps_removed)}\n", style="red")
    if diff.steps_added:
        lines.append(f"steps added: {', '.join(diff.steps_added)}\n", style="green")
    if diff.order_changed:
        lines.append(
            f"step order changed:\n  {' → '.join(diff.sequence_a)}\n  {' → '.join(diff.sequence_b)}\n",
            style="yellow",
        )
    for change in diff.changes:
        where = f"{change.step}.{change.field}" if change.field else change.step
        style = {"removed": "red", "became_empty": "red", "type_changed": "yellow"}.get(
            change.change, "green"
        )
        lines.append(f"{where}: {change.change}", style=style)
        if change.before is not None or change.after is not None:
            lines.append(f" ({change.before} → {change.after})", style="dim")
        lines.append("\n")
    for metric in diff.metrics:
        pct = f"{metric.pct:+.0f}%" if metric.pct is not None else "n/a"
        lines.append(
            f"{metric.metric}: {metric.before:.4g} → {metric.after:.4g} ({pct})\n",
            style="red" if metric.regression else "dim",
        )
    if diff.is_empty:
        lines.append("No structural differences.\n", style="green")
    return Panel(lines, title="tervic diff", expand=False)
