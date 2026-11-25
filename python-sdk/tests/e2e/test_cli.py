"""Every CLI command, with --json, and the exit codes (0 pass, 1 fail, 2 Tervic/usage error)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
FIXTURES = Path(__file__).parents[1] / "fixtures"
from helpers import node, record
from typer.testing import CliRunner

from tervic.cli import app
from tervic.core.run import Run


def cli(*args: str, input: str | None = None) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), input=input)
    return result.exit_code, result.output


def cli_json(*args: str) -> tuple[int, Any]:
    code, output = cli(*args, "--json")
    return code, json.loads(output)


def failing(run: Run) -> None:
    step = run.step_start("retrieve", "retriever", {"q": "x"}, source="agent/nodes.py:12")
    run.step_end(step, {"documents": []}, state_mode="update")
    node(run, "answer", {"q": "x", "documents": []}, {"answer": "idk"}, reads=["documents"])


def passing(run: Run) -> str:
    node(run, "retrieve", {"q": "x"}, {"documents": ["d"]}, kind="retriever")
    return "answer"


def test_version_and_help() -> None:
    code, out = cli("--help")
    assert code == 0 and "silently broke" in out
    assert cli_json("version")[1]["version"] == "1.0.0"


def test_init_creates_config_and_detects_framework(project: Path) -> None:
    (project / "agent.py").write_text("from langgraph.graph import StateGraph\n", encoding="utf-8")
    code, data = cli_json("init")
    assert code == 0 and data["framework"] == "langgraph"
    assert (project / "tervic.toml").is_file() and (project / ".tervic" / ".gitignore").is_file()
    assert "tervic.attach" in data["snippet"]
    code, out = cli("init")
    assert code == 0 and "already exist" in out


def test_check_exit_codes_and_fail_on() -> None:
    assert cli("check")[0] == 2  # nothing recorded yet
    record(passing)
    code, data = cli_json("check")
    assert code == 0 and data["verdict"] == "pass"
    record(failing)
    code, out = cli("check")
    assert code == 1 and "Root cause: retrieve" in out and "tervic fix" in out
    record(lambda run: node(run, "plan", {"q": 1}, {}) and "x")  # TV001 warn only
    assert cli("check")[0] == 0
    assert cli("check", "--fail-on", "warn")[0] == 1
    assert cli("check", "--fail-on", "nope")[0] == 2
    code, data = cli_json("check", "--all")
    assert code == 1 and len(data["runs"]) == 3


def test_check_all_since_commit(project: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    record(passing)
    code, data = cli_json("check", "--all-since", sha)
    assert code == 0 and len(data["runs"]) == 1


def test_runs_show_and_html(project: Path) -> None:
    report = record(failing)
    code, rows = cli_json("runs")
    assert code == 0 and rows[0]["run_id"] == report.run_id and rows[0]["verdict"] == "fail"
    code, out = cli("runs")
    assert code == 0 and report.run_id in out
    code, data = cli_json("show", "last")
    assert code == 0 and data["groups"][0]["root_cause_step_name"] == "retrieve"
    code, out = cli("show")
    assert code == 0 and "retrieve" in out
    code, data = cli_json("show", "--html", "--no-open")
    html = Path(data["html"]).read_text(encoding="utf-8")
    assert "<script" in html and "http://" not in html.split("<script")[0].lower()
    assert "src=" not in html and "<link" not in html  # self-contained, no external requests
    assert cli("show", "NOPE")[0] == 2


def test_diff_and_baselines() -> None:
    good = record(passing, name="rag")
    bad = record(failing, name="rag")
    code, data = cli_json("baseline", "set", good.run_id, "--name", "happy")
    assert code == 0 and data["name"] == "happy"
    code, items = cli_json("baseline", "list")
    assert items[0]["name"] == "happy"
    code, diff = cli_json("diff", "baseline:happy", bad.run_id)
    assert code == 0 and diff["steps_added"] == ["answer"]
    code, out = cli("diff", good.run_id, bad.run_id)
    assert code == 0 and "became_empty" in out
    assert cli("diff", "baseline:none", "last")[0] == 2
    code, data = cli_json("baseline", "rm", "happy")
    assert code == 0 and data["removed"] is True
    assert cli("baseline", "rm", "happy")[0] in (1, 2)


def test_fix_command(project: Path) -> None:
    record(failing)
    code, data = cli_json("fix")
    assert code == 0 and "Root cause: step `retrieve` (agent/nodes.py:12)" in data["prompt"]
    code, out = cli("fix", "--no-copy", "--format", "cursor", "--write", "out/fix.md")
    assert code == 0 and (project / "out" / "fix.md").read_text(encoding="utf-8") == out
    assert cli("fix", "--format", "nope")[0] == 2


def test_report_formats(project: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    record(failing)
    record(passing)
    code, _ = cli("report", "--all", "--format", "sarif", "-o", "tervic.sarif")
    assert code == 0
    sarif = json.loads((project / "tervic.sarif").read_text(encoding="utf-8"))
    schema = json.loads((FIXTURES / "sarif-2.1.0.json").read_text(encoding="utf-8"))
    jsonschema.validate(sarif, schema)
    result = sarif["runs"][0]["results"][0]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "agent/nodes.py"

    assert cli("report", "--all", "-f", "junit", "-o", "junit.xml")[0] == 0
    root = ET.parse(project / "junit.xml").getroot()
    assert root.tag == "testsuites" and int(root.get("failures", "0")) >= 1
    assert len(root.findall("testsuite")) == 2

    code, out = cli("report", "last", "-f", "markdown")
    assert code == 0 and "<!-- tervic-report -->" in out
    code, out = cli("report", "-f", "json")
    assert code == 0 and json.loads(out)["verdict"] == "pass"
    code, data = cli_json("report", "-f", "html", "-o", "r.html")
    assert code == 0 and (project / "r.html").is_file()
    assert cli("report", "-f", "pdf")[0] == 2


def test_contracts_infer_and_validate(project: Path) -> None:
    for _ in range(2):
        record(passing)
    code, data = cli_json("contracts", "infer", "-o", "suggested.toml")
    assert code == 0 and data["runs_used"] == 2 and "retrieve" in data["contracts"]
    assert "[contracts.retrieve]" in (project / "suggested.toml").read_text(encoding="utf-8")
    assert cli("contracts", "validate")[0] == 2  # no contracts declared yet
    (project / "tervic.toml").write_text(
        (project / "suggested.toml").read_text(encoding="utf-8"), encoding="utf-8")
    from tervic.config import reset_config

    reset_config()
    assert cli("contracts", "validate")[0] == 0
    record(failing)
    code, data = cli_json("contracts", "validate")
    assert code == 1 and data["violations"]


def test_gc_doctor_and_key(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in range(3):
        record(passing)
    code, data = cli_json("gc", "--keep", "1")
    assert code == 0 and len(data["removed"]) == 2
    code, data = cli_json("doctor")
    assert code == 0 and data["ok"] is True
    assert any(c["check"] == "import tervic" for c in data["checks"])
    assert cli("key", "set", "--provider", "bogus", "--key", "x")[0] == 2

    stored: dict[str, str] = {}
    import tervic.analyze.judge as judge

    monkeypatch.setattr(judge, "set_key", lambda provider, key: stored.update({provider: key}) or "OS keyring")
    code, data = cli_json("key", "set", "--provider", "anthropic", "--key", "secret-value")
    assert code == 0 and stored == {"anthropic": "secret-value"}


def test_no_color_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    record(failing)
    code, out = cli("check")
    assert code == 1 and "\x1b[" not in out


def test_python_dash_m_entry_point(project: Path) -> None:
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "tervic", "version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1.0.0"
