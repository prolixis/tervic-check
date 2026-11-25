"""Start the Tervic MCP server and call every tool through the official MCP client (in memory)."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from helpers import node, record

import tervic
from tervic.core.run import Run

mcp = pytest.importorskip("mcp")

AGENT = '''
import tervic

@tervic.step(kind="retriever")
def retrieve(state):
    return {**state, "documents": ["fixed doc"]}

def run(question):
    return retrieve({"question": question})
'''


def _text(result: Any) -> Any:
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured["result"] if set(structured) == {"result"} else structured
    values = []
    for block in result.content:
        try:
            values.append(json.loads(block.text))
        except json.JSONDecodeError:
            values.append(block.text)
    return values[0] if len(values) == 1 else values


def failing(run: Run) -> None:
    step = run.step_start("retrieve", "retriever", {"question": "q"}, source="agent.py:4")
    run.step_end(step, {"documents": []}, state_mode="update")
    node(run, "answer", {"documents": []}, {"answer": "idk"}, reads=["documents"])


async def test_every_tool(project: Path) -> None:
    from mcp.client.client import Client

    from tervic.mcp.server import INSTRUCTIONS, build_server

    (project / "mcpagent.py").write_text(textwrap.dedent(AGENT), encoding="utf-8")
    (project / "tervic.toml").write_text('[replay]\nentrypoint = "mcpagent:run"\n', encoding="utf-8")
    sys.path.insert(0, str(project))
    tervic.configure()
    try:
        with tervic.run("agent", input="q") as recorded:
            failing(recorded)
        good = record(lambda run: "fine", name="agent")

        server = build_server()
        async with Client(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {"list_runs", "get_findings", "get_step", "get_fix_prompt", "diff_runs",
                                  "run_replay"}
            assert tools["get_findings"].annotations.read_only_hint is True
            assert tools["run_replay"].annotations.read_only_hint is False
            assert client.instructions == INSTRUCTIONS or INSTRUCTIONS in str(client.server_info)

            runs = _text(await client.call_tool("list_runs", {"limit": 5}))
            assert {r["run_id"] for r in runs} >= {recorded.run_id, good.run_id}

            findings = _text(await client.call_tool("get_findings", {"run_id": recorded.run_id}))
            assert findings["verdict"] == "fail"
            assert findings["root_causes"][0]["root_cause_step"] == "retrieve"
            assert findings["root_causes"][0]["source"] == "agent.py:4"

            step = _text(await client.call_tool("get_step", {"run_id": recorded.run_id, "step_name": "retrieve"}))
            assert step["occurrences"][0]["output"] == {"documents": []}
            missing = _text(await client.call_tool("get_step", {"run_id": recorded.run_id, "step_name": "x"}))
            assert "error" in missing

            prompt = _text(await client.call_tool("get_fix_prompt", {"run_id": recorded.run_id}))
            assert "Root cause: step `retrieve`" in prompt

            diff = _text(await client.call_tool("diff_runs", {"a": recorded.run_id, "b": good.run_id}))
            assert diff["steps_removed"] == ["retrieve", "answer"]

            replayed = _text(await client.call_tool("run_replay", {"run_id": recorded.run_id,
                                                                    "from_step": "retrieve"}))
            assert replayed["before_verdict"] == "fail" and replayed["after_verdict"] == "pass"
    finally:
        sys.path.remove(str(project))
        sys.modules.pop("mcpagent", None)


def test_stdio_entry_point_is_wired() -> None:
    from typer.testing import CliRunner

    from tervic.cli import app

    result = CliRunner().invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0 and "MCP server" in result.output
