"""The ``tervic`` command line. Every command accepts ``--json`` for scripting.

Exit codes: 0 = pass, 1 = fail (a check failed / a contract was violated), 2 = Tervic or usage error.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from tervic._version import __version__

app = typer.Typer(
    name="tervic",
    help="Tervic tells you which step of your AI agent silently broke, and stops it from shipping.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
baseline_app = typer.Typer(
    help="Manage golden-run baselines (stored in .tervic/baselines/, committable).", no_args_is_help=True
)
contracts_app = typer.Typer(help="Infer or validate step contracts.", no_args_is_help=True)
key_app = typer.Typer(help="Manage the optional LLM judge key.", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")
app.add_typer(contracts_app, name="contracts")
app.add_typer(key_app, name="key")

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


class Exit:
    PASS = 0
    FAIL = 1
    ERROR = 2


@app.callback()
def _startup() -> None:
    """Tervic tells you which step of your AI agent silently broke, and stops it from shipping."""
    global _flushed
    _flushed = False


def _console() -> Any:
    from tervic.report.console import make_console

    return make_console(no_color=bool(os.environ.get("NO_COLOR")))


def _emit_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def _fail(message: str, as_json: bool = False, code: int = Exit.ERROR) -> NoReturn:
    if as_json:
        _emit_json({"error": message})
    else:
        typer.echo(f"tervic: {message}", err=True)
    raise typer.Exit(code)


_flushed = False


def _store() -> Any:
    """The project's store, after flushing anything still queued in this process (e.g. CrewAI events)."""
    global _flushed
    from tervic.api import flush, store_for

    if not _flushed:
        flush()
        _flushed = True
    return store_for()


def _resolve(ref: str | None, as_json: bool) -> str:
    from tervic.store.local import RunNotFound

    try:
        return str(_store().resolve(ref))
    except RunNotFound as exc:
        _fail(str(exc), as_json)


def _commit_time(sha: str) -> float:
    try:
        out = subprocess.run(
            ["git", "show", "-s", "--format=%ct", sha], capture_output=True, text=True, timeout=5, check=True
        )
        return float(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise typer.BadParameter(f"cannot read the commit time of {sha!r}: {exc}") from exc


def _runs_for(run: str | None, all_runs: bool, all_since: str | None, as_json: bool) -> list[str]:
    store = _store()
    if all_runs or all_since:
        since = _commit_time(all_since) if all_since else None
        rows = store.list_runs(limit=100_000, since_ts=since)
        if not rows:
            _fail("No runs recorded" + (f" since {all_since}" if all_since else "") + ".", as_json)
        return [row.run_id for row in reversed(rows)]
    return [_resolve(run, as_json)]


# ---------------------------------------------------------------- init

_SNIPPETS = {
    "langgraph": "import tervic\napp = tervic.attach(graph.compile())",
    "openai-agents": "import tervic\ntervic.attach_openai_agents()",
    "crewai": "import tervic\ntervic.attach_crewai()",
    "python": (
        "import tervic\n\n@tervic.step\ndef retrieve(state): ...\n\n"
        'with tervic.run("my-agent") as run:\n    pipeline(question)\nprint(run.verdict)'
    ),
}

_DEFAULT_TOML = """# Tervic configuration. See docs/QUICKSTART.md.
[tervic]
mode = "dev"            # dev | ci | prod
store_dir = ".tervic"
fail_on = "error"       # error | warn
retention_runs = 200

[budget]
# cost_usd = 0.50
# latency_ms = 60000
# tokens = 200000

[detectors]
disable = []            # e.g. ["TV502"]
loop_threshold = 5

[judge]
enabled = false
provider = "anthropic"
max_calls = 10

[replay]
# entrypoint = "app.main:run"
"""


def detect_framework(root: Path) -> str:
    texts: list[str] = []
    for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "Pipfile"):
        path = root / name
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="ignore").lower())
    for path in list(root.rglob("*.py"))[:400]:
        if ".venv" in path.parts or "site-packages" in path.parts or ".tervic" in path.parts:
            continue
        try:
            texts.append(path.read_text(encoding="utf-8", errors="ignore")[:20_000].lower())
        except OSError:
            continue
    blob = "\n".join(texts)
    if "langgraph" in blob:
        return "langgraph"
    if "openai-agents" in blob or "from agents import" in blob or "import agents" in blob:
        return "openai-agents"
    if "crewai" in blob:
        return "crewai"
    return "python"


@app.command()
def init(json_out: JsonOpt = False) -> None:
    """Create tervic.toml and .tervic/, detect your framework and print the 2-line snippet."""
    from tervic.store.local import LocalStore

    root = Path.cwd()
    created: list[str] = []
    toml = root / "tervic.toml"
    if not toml.exists():
        toml.write_text(_DEFAULT_TOML, encoding="utf-8")
        created.append("tervic.toml")
    store = LocalStore(root / ".tervic")
    existed = (root / ".tervic" / ".gitignore").exists()
    store.ensure()
    if not existed:
        created.append(".tervic/ (with its own .gitignore)")
    framework = detect_framework(root)
    if json_out:
        _emit_json({"created": created, "framework": framework, "snippet": _SNIPPETS[framework]})
        return
    console = _console()
    for item in created:
        console.print(f"[green]created[/] {item}")
    if not created:
        console.print("tervic.toml and .tervic/ already exist.")
    console.print(f"\nDetected framework: [bold]{framework}[/]\nAdd this to your agent:\n")
    console.print(_SNIPPETS[framework], markup=False)
    console.print("\nThen run your agent and: [bold]tervic check[/]")


# ---------------------------------------------------------------- runs / show / check


@app.command()
def runs(
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many runs to list.")] = 20,
    verdict: Annotated[str | None, typer.Option(help="Only runs with this verdict.")] = None,
    json_out: JsonOpt = False,
) -> None:
    """List recent runs (verdict, name, duration, cost, git sha)."""
    rows = _store().list_runs(limit=limit, verdict=verdict)
    if json_out:
        _emit_json([row.model_dump() for row in rows])
        return
    if not rows:
        typer.echo("No runs recorded yet.")
        return
    from tervic.report.console import runs_table

    _console().print(runs_table(rows))


def _load_view(run_id: str) -> Any:
    from tervic.analyze.model import build_view

    return build_view(_store().load_events(run_id))


@app.command()
def show(
    run: Annotated[str, typer.Argument(help="Run id, id prefix or 'last'.")] = "last",
    html: Annotated[bool, typer.Option("--html", help="Write the HTML report and open it.")] = False,
    no_open: Annotated[bool, typer.Option("--no-open", help="With --html: do not open a browser.")] = False,
    json_out: JsonOpt = False,
) -> None:
    """Step tree with timings, cost and findings inline."""
    from tervic.api import check

    run_id = _resolve(run, json_out)
    report = check(run_id, write=False)
    if html:
        from tervic.report.html import render

        path = _store().run_dir(run_id) / "report.html"
        path.write_text(render(report, _load_view(run_id)), encoding="utf-8")
        if not no_open:
            import webbrowser

            webbrowser.open(path.resolve().as_uri())
        if json_out:
            _emit_json({"html": str(path)})
        else:
            typer.echo(f"HTML report: {path}")
        return
    if json_out:
        _emit_json(report.model_dump(mode="json"))
        return
    from tervic.report.console import print_report

    print_report(_console(), report, tree=True)


@app.command("check")
def check_cmd(
    run: Annotated[str | None, typer.Argument(help="Run id, id prefix or 'last' (default).")] = None,
    all_since: Annotated[
        str | None, typer.Option("--all-since", help="Check every run since this commit.")
    ] = None,
    all_runs: Annotated[bool, typer.Option("--all", help="Check every stored run.")] = False,
    fail_on: Annotated[str | None, typer.Option("--fail-on", help="error (default) or warn.")] = None,
    json_out: JsonOpt = False,
) -> None:
    """Analyze runs. Exit 0 = pass, 1 = fail, 2 = Tervic internal error."""
    from tervic.api import check
    from tervic.config import get_config

    try:
        config = get_config()
        if fail_on:
            if fail_on not in ("error", "warn"):
                _fail("--fail-on must be 'error' or 'warn'", json_out)
            config = config.model_copy(update={"fail_on": fail_on})
        reports = [check(rid, config=config) for rid in _runs_for(run, all_runs, all_since, json_out)]
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(f"internal error: {type(exc).__name__}: {exc}", json_out)
    failed = any(r.verdict == "fail" for r in reports)
    if json_out:
        _emit_json(
            {
                "verdict": "fail"
                if failed
                else ("warn" if any(r.verdict == "warn" for r in reports) else "pass"),
                "runs": [r.model_dump(mode="json") for r in reports],
            }
        )
    else:
        from tervic.report.console import print_report

        console = _console()
        for index, report in enumerate(reports):
            if index:
                console.rule()
            print_report(console, report)
        if failed:
            console.print(
                f"\nNext: [bold]tervic fix {reports[-1].run_id}[/] for a ready-to-paste fix prompt."
            )
    raise typer.Exit(Exit.FAIL if failed else Exit.PASS)


# ---------------------------------------------------------------- diff / baselines


def _events_for(ref: str, as_json: bool) -> tuple[str, Any]:
    from tervic.analyze.model import build_view

    store = _store()
    if ref.startswith("baseline:"):
        name = ref.split(":", 1)[1]
        events = store.baseline_events(name)
        if events is None:
            _fail(f"No baseline named {name!r}.", as_json)
        return ref, build_view(events)
    run_id = _resolve(ref, as_json)
    return run_id, build_view(store.load_events(run_id))


@app.command()
def diff(
    a: Annotated[str, typer.Argument(help="Run id, 'last' or 'baseline:<name>'.")],
    b: Annotated[str, typer.Argument(help="Run id, 'last' or 'baseline:<name>'.")] = "last",
    json_out: JsonOpt = False,
) -> None:
    """Structural, step-sequence and cost/latency/token diff between two runs."""
    from tervic.analyze.baseline import diff_runs
    from tervic.config import get_config

    _, view_a = _events_for(a, json_out)
    _, view_b = _events_for(b, json_out)
    result = diff_runs(view_a, view_b, get_config().detectors.baseline_regression_pct)
    if json_out:
        _emit_json(result.model_dump())
        return
    from tervic.report.console import diff_view

    _console().print(diff_view(result))


@baseline_app.command("set")
def baseline_set(
    run: Annotated[str, typer.Argument(help="Run id, id prefix or 'last'.")],
    name: Annotated[
        str | None, typer.Option("--name", help="Baseline name (default: the run's name).")
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Store a golden run in .tervic/baselines/<name>/ (commit it to share it)."""
    store = _store()
    run_id = _resolve(run, json_out)
    row = store.get_row(run_id)
    chosen = name or (row.name if row else run_id)
    path = store.save_baseline(chosen, run_id)
    if json_out:
        _emit_json({"name": chosen, "run_id": run_id, "path": str(path)})
    else:
        typer.echo(f"Baseline '{chosen}' set from run {run_id} ({path}).")


@baseline_app.command("list")
def baseline_list(json_out: JsonOpt = False) -> None:
    """List baselines."""
    items = _store().list_baselines()
    if json_out:
        _emit_json(items)
        return
    if not items:
        typer.echo("No baselines. Create one with: tervic baseline set last --name happy-path")
    for item in items:
        typer.echo(f"{item['name']}\t{item['run_id']}\t{item.get('run_name') or ''}")


@baseline_app.command("rm")
def baseline_rm(name: str, json_out: JsonOpt = False) -> None:
    """Remove a baseline."""
    removed = _store().remove_baseline(name)
    if json_out:
        _emit_json({"removed": removed, "name": name})
    elif removed:
        typer.echo(f"Removed baseline '{name}'.")
    else:
        _fail(f"No baseline named {name!r}.")
    if not removed:
        raise typer.Exit(Exit.FAIL)


# ---------------------------------------------------------------- replay / fix / report


@app.command()
def replay(
    run: Annotated[str, typer.Argument(help="Run id, id prefix or 'last'.")],
    from_step: Annotated[str | None, typer.Option("--from", help="First step to run live.")] = None,
    offline: Annotated[
        bool, typer.Option("--offline", help="Serve every LLM call from the recording.")
    ] = False,
    entrypoint: Annotated[
        str | None, typer.Option(help="package.module:function (overrides tervic.toml).")
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Re-run from a step: earlier steps come from the recording (no LLM calls, no cost)."""
    from tervic.replay.replayer import ReplayError
    from tervic.replay.replayer import replay as do_replay

    run_id = _resolve(run, json_out)
    try:
        result = do_replay(run_id, from_step, offline=offline, entrypoint=entrypoint)
    except ReplayError as exc:
        _fail(str(exc), json_out)
    if json_out:
        _emit_json(result.model_dump())
    else:
        console = _console()
        from tervic.report.console import verdict_text

        line = verdict_text(result.before_verdict)
        line.append("  →  ")
        line.append_text(verdict_text(result.after_verdict))
        console.print(
            f"Replayed run {run_id} from [bold]{from_step or 'the start'}[/] as {result.new_run_id}"
        )
        console.print(line)
        console.print(
            f"cassette hits: {result.cassette_hits} (earlier steps and LLM calls served from the "
            f"recording) · misses: {result.cassette_misses}"
        )
        if result.live:
            console.print(f"ran live (not replayable): {', '.join(result.live)}", style="dim")
        for key in result.fixed:
            console.print(f"[green]fixed[/] {key}")
        for key in result.still_failing:
            console.print(f"[red]still failing[/] {key}")
        for key in result.new:
            console.print(f"[yellow]new[/] {key}")
        if result.error:
            console.print(f"[red]the agent raised:[/] {result.error}")
    raise typer.Exit(Exit.FAIL if result.after_verdict == "fail" or result.error else Exit.PASS)


@app.command()
def fix(
    run: Annotated[str, typer.Argument(help="Run id, id prefix or 'last'.")] = "last",
    fmt: Annotated[str, typer.Option("--format", help="claude | cursor | plain")] = "plain",
    write: Annotated[Path | None, typer.Option("--write", help="Also write the prompt to this file.")] = None,
    no_copy: Annotated[bool, typer.Option("--no-copy", help="Do not copy to the clipboard.")] = False,
    json_out: JsonOpt = False,
) -> None:
    """Print (and copy) a ready-to-paste fix prompt for Claude Code / Cursor. Offline, no LLM."""
    from tervic.analyze.model import build_view
    from tervic.api import check
    from tervic.fix.prompt import build_prompt, copy_to_clipboard

    if fmt not in ("claude", "cursor", "plain"):
        _fail("--format must be claude, cursor or plain", json_out)
    run_id = _resolve(run, json_out)
    store = _store()
    report = check(run_id, write=False)
    view = build_view(store.load_events(run_id))
    baseline = None
    if report.baseline:
        events = store.baseline_events(report.baseline)
        baseline = build_view(events) if events else None
    text = build_prompt(report, view, baseline, fmt)  # type: ignore[arg-type]
    if write is not None:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(text, encoding="utf-8")
    copied = False if no_copy or json_out else copy_to_clipboard(text)
    if json_out:
        _emit_json({"run_id": run_id, "prompt": text, "written": str(write) if write else None})
        return
    sys.stdout.write(text)
    if copied:
        typer.echo("(copied to clipboard)", err=True)


@app.command()
def report(
    run: Annotated[str | None, typer.Argument(help="Run id, id prefix or 'last'.")] = None,
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="html | junit | sarif | markdown | json")
    ] = "html",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output file (default: stdout).")
    ] = None,
    all_runs: Annotated[
        bool, typer.Option("--all", help="Include every stored run (junit/sarif/markdown).")
    ] = False,
    all_since: Annotated[
        str | None, typer.Option("--all-since", help="Include runs since this commit.")
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Export a report as HTML, JUnit XML, SARIF 2.1.0, Markdown (PR comment) or JSON."""
    from tervic.api import check

    if fmt not in ("html", "junit", "sarif", "markdown", "json"):
        _fail("--format must be html, junit, sarif, markdown or json", json_out)
    run_ids = _runs_for(run, all_runs, all_since, json_out)
    reports = [check(rid, write=False) for rid in run_ids]
    if fmt == "html":
        from tervic.report.html import render as render_html

        text = render_html(reports[-1], _load_view(run_ids[-1]))
    elif fmt == "junit":
        from tervic.report.junit import render as render_junit

        text = render_junit(reports)
    elif fmt == "sarif":
        from tervic.report.sarif import render as render_sarif

        text = render_sarif(reports)
    elif fmt == "markdown":
        from tervic.report.markdown import render as render_md

        text = render_md(reports)
    else:
        payload = [r.model_dump(mode="json") for r in reports]
        text = json.dumps(payload[0] if len(payload) == 1 else payload, indent=2, default=str) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        if json_out:
            _emit_json({"format": fmt, "output": str(output), "runs": run_ids})
        else:
            typer.echo(f"Wrote {fmt} report to {output}")
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------- contracts


@contracts_app.command("infer")
def contracts_infer(
    runs_n: Annotated[int, typer.Option("--runs", help="Use the last N passing runs.")] = 20,
    name: Annotated[str | None, typer.Option("--name", help="Only runs with this name.")] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("tervic.contracts.suggested.toml"),
    json_out: JsonOpt = False,
) -> None:
    """Suggest contracts from recent successful runs. Review the file before adding it to tervic.toml."""
    from tervic.analyze.contracts import infer, to_toml
    from tervic.analyze.model import build_view

    store = _store()
    rows = [
        r for r in store.list_runs(limit=500, name=name) if r.verdict == "pass" and r.status == "completed"
    ]
    views = [build_view(store.load_events(r.run_id)) for r in rows[:runs_n]]
    if not views:
        _fail("No passing runs to learn from yet.", json_out)
    contracts = infer(views)
    output.write_text(to_toml(contracts), encoding="utf-8")
    if json_out:
        _emit_json(
            {
                "runs_used": len(views),
                "output": str(output),
                "contracts": {
                    k: v.model_dump(by_alias=True, exclude_defaults=True) for k, v in contracts.items()
                },
            }
        )
    else:
        typer.echo(
            f"Learned contracts for {len(contracts)} step(s) from {len(views)} run(s); wrote {output}."
        )
        typer.echo("Review it, then copy the sections you want into tervic.toml.")


@contracts_app.command("validate")
def contracts_validate(
    run: Annotated[str, typer.Argument(help="Run id, id prefix or 'last'.")] = "last",
    json_out: JsonOpt = False,
) -> None:
    """Check a run against the declared contracts. Exit 1 if any contract is violated."""
    from tervic.analyze.contracts import check_step
    from tervic.config import get_config

    config = get_config()
    if not config.contracts:
        _fail("No contracts declared (add [contracts.<step>] to tervic.toml).", json_out)
    view = _load_view(_resolve(run, json_out))
    violations = []
    checked = set()
    for step in view.steps:
        spec = config.contracts.get(step.name)
        if spec is None or step.error is not None:
            continue
        checked.add(step.name)
        for violation in check_step(step, spec):
            violations.append(
                {
                    "step": step.name,
                    "detector": violation.detector_id,
                    "field": violation.field,
                    "message": violation.message,
                }
            )
    missing = sorted(set(config.contracts) - checked)
    if json_out:
        _emit_json({"violations": violations, "steps_checked": sorted(checked), "steps_not_run": missing})
    else:
        for v in violations:
            typer.echo(f"✗ {v['step']}: {v['message']} ({v['detector']})")
        if missing:
            typer.echo(f"not run in this run: {', '.join(missing)}")
        if not violations:
            typer.echo(f"✓ {len(checked)} step(s) meet their contracts.")
    raise typer.Exit(Exit.FAIL if violations else Exit.PASS)


# ---------------------------------------------------------------- key / mcp / gc / doctor


@key_app.command("set")
def key_set(
    provider: Annotated[str, typer.Option("--provider", help="openai | anthropic | gemini")],
    key: Annotated[str | None, typer.Option("--key", help="The API key (default: read from stdin).")] = None,
    json_out: JsonOpt = False,
) -> None:
    """Store the judge key in the OS keyring (pip install tervic[judge])."""
    if provider not in ("openai", "anthropic", "gemini"):
        _fail("--provider must be openai, anthropic or gemini", json_out)
    value = (
        key
        if key is not None
        else (
            sys.stdin.readline().strip()
            if not sys.stdin.isatty()
            else typer.prompt("API key", hide_input=True)
        )
    )
    if not value:
        _fail("empty key", json_out)
    try:
        from tervic.analyze.judge import set_key

        where = set_key(provider, value)
    except ImportError:
        _fail(
            "keyring is not installed. pip install 'tervic[judge]' or set the provider's env var.", json_out
        )
    except Exception as exc:
        _fail(f"could not store the key: {exc}", json_out)
    if json_out:
        _emit_json({"provider": provider, "stored_in": where})
    else:
        typer.echo(f"Stored the {provider} key in the {where}. Enable the judge with [judge] enabled = true.")


@app.command()
def mcp() -> None:
    """Start the Tervic MCP server on stdio (pip install tervic[mcp])."""
    try:
        from tervic.mcp.server import run_stdio
    except ImportError as exc:
        _fail(f"the MCP server needs the 'mcp' package: pip install 'tervic[mcp]' ({exc})")
    run_stdio()


@app.command()
def gc(
    keep: Annotated[
        int | None, typer.Option("--keep", help="Runs to keep (default: retention_runs).")
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Delete old runs beyond the retention limit."""
    from tervic.config import get_config

    removed = _store().gc(keep if keep is not None else get_config().retention_runs)
    if json_out:
        _emit_json({"removed": removed})
    else:
        typer.echo(f"Removed {len(removed)} run(s).")


@app.command()
def doctor(json_out: JsonOpt = False) -> None:
    """Check the installation, optional adapters, store permissions and index health."""
    import platform
    import sqlite3
    from importlib import metadata

    from tervic.config import find_config_file, get_config
    from tervic.core.safety import internal_error_counts

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("python", sys.version_info >= (3, 10), platform.python_version())
    add("tervic", True, __version__)
    probe_code = "import time;t=time.perf_counter();import tervic;print((time.perf_counter()-t)*1000)"
    probe = subprocess.run(
        [sys.executable, "-c", probe_code], check=False, capture_output=True, text=True, timeout=30
    )
    try:
        import_ms = float(probe.stdout.strip().splitlines()[-1])
        add("import tervic", import_ms < 150, f"{import_ms:.1f} ms (target < 150 ms)")
    except (ValueError, IndexError):
        add("import tervic", False, probe.stderr.strip()[-200:] or "failed")
    for extra, dist in (
        ("langgraph", "langgraph"),
        ("openai-agents", "openai-agents"),
        ("crewai", "crewai"),
        ("otel", "opentelemetry-sdk"),
        ("mcp", "mcp"),
        ("judge", "keyring"),
    ):
        try:
            add(f"extra [{extra}]", True, f"{dist} {metadata.version(dist)}")
        except metadata.PackageNotFoundError:
            add(f"extra [{extra}]", True, "not installed (optional)")
    try:
        config = get_config()
        source = find_config_file()
        add("config", True, str(source) if source else "defaults (no tervic.toml)")
    except Exception as exc:
        add("config", False, f"{type(exc).__name__}: {exc}")
        config = None
    if config is not None:
        root = config.store_path()
        try:
            from tervic.store.local import LocalStore

            store = LocalStore(root)
            store.ensure()
            write_test = root / ".write-test"
            write_test.write_text("ok", encoding="utf-8")
            write_test.unlink()
            add("store writable", True, str(root))
            with sqlite3.connect(store.index_path) as db:
                status = db.execute("PRAGMA integrity_check").fetchone()[0]
                count = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            add("index", status == "ok", f"{status}, {count} run(s)")
        except Exception as exc:
            add("store writable", False, f"{type(exc).__name__}: {exc}")
    errors = internal_error_counts()
    add("internal errors this process", not errors, json.dumps(errors) if errors else "none")
    ok = all(c["ok"] for c in checks)
    if json_out:
        _emit_json({"ok": ok, "checks": checks})
    else:
        from rich.markup import escape

        console = _console()
        for c in checks:
            mark = "[green]✓[/]" if c["ok"] else "[red]✗[/]"
            console.print(f"{mark} {escape(c['check'])}: {escape(str(c['detail']))}")
    raise typer.Exit(Exit.PASS if ok else Exit.ERROR)


@app.command()
def version(json_out: JsonOpt = False) -> None:
    """Print the Tervic version."""
    if json_out:
        _emit_json({"version": __version__})
    else:
        typer.echo(__version__)


def _safe_streams() -> None:
    """Never crash on a console that cannot encode a character (e.g. cp1252 on Windows)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")


def main() -> None:
    _safe_streams()
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    main()
