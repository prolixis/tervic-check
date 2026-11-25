"""Replay from a step: earlier steps come from the cassette (no LLM calls), later steps run live."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import tervic
from tervic.replay.replayer import ReplayError, replay

AGENT = '''
import tervic

CALLS = {"llm": 0}
FIX = {"enabled": False}


@tervic.step(kind="llm")
def expensive_llm(state):
    CALLS["llm"] += 1
    return {**state, "plan": "search docs"}


@tervic.step(kind="retriever")
def retrieve(state):
    docs = ["doc about tervic"] if FIX["enabled"] else []
    return {**state, "documents": docs}


@tervic.step
def answer(state):
    return {**state, "answer": f"{len(state['documents'])} docs"}


def run(question):
    state = expensive_llm({"question": question})
    state = retrieve(state)
    return answer(state)["answer"]
'''


@pytest.fixture
def agent(project: Path) -> object:
    (project / "myagent.py").write_text(textwrap.dedent(AGENT), encoding="utf-8")
    (project / "tervic.toml").write_text('[replay]\nentrypoint = "myagent:run"\n', encoding="utf-8")
    sys.path.insert(0, str(project))
    sys.modules.pop("myagent", None)
    import myagent  # type: ignore[import-not-found]

    tervic.configure()  # re-read tervic.toml
    yield myagent
    sys.modules.pop("myagent", None)
    sys.path.remove(str(project))


def test_replay_from_step_skips_earlier_llm_and_fixes(agent: object) -> None:
    with tervic.run("myagent", input="what is tervic?") as run:
        agent.run("what is tervic?")  # type: ignore[attr-defined]
    assert run.verdict == "fail"
    assert agent.CALLS["llm"] == 1  # type: ignore[attr-defined]

    agent.FIX["enabled"] = True  # type: ignore[attr-defined]  # the developer fixes the retriever
    result = replay(run.run_id, "retrieve")
    assert result.cassette_hits == 1  # expensive_llm served from the recording
    assert agent.CALLS["llm"] == 1  # type: ignore[attr-defined]  # ...so it was not called again
    assert result.before_verdict == "fail" and result.after_verdict == "pass"
    assert any(key.startswith("TV301") for key in result.fixed)
    assert result.new_run_id and result.new_run_id != run.run_id


def test_replay_errors(agent: object, project: Path) -> None:
    with tervic.run("myagent", input="q") as run:
        agent.run("q")  # type: ignore[attr-defined]
    with pytest.raises(ReplayError, match="not in run"):
        replay(run.run_id, "no_such_step")
    with pytest.raises(ReplayError, match="package.module:function"):
        replay(run.run_id, "retrieve", entrypoint="nocolon")
    with pytest.raises(ReplayError, match="Could not import"):
        replay(run.run_id, "retrieve", entrypoint="missing_module:run")
    (project / "tervic.toml").write_text("", encoding="utf-8")
    tervic.configure()
    with pytest.raises(ReplayError, match="No entry point"):
        replay(run.run_id, "retrieve")


def test_replay_cli(agent: object) -> None:
    from typer.testing import CliRunner

    from tervic.cli import app

    with tervic.run("myagent", input="q") as run:
        agent.run("q")  # type: ignore[attr-defined]
    agent.FIX["enabled"] = True  # type: ignore[attr-defined]
    result = CliRunner().invoke(app, ["replay", run.run_id, "--from", "retrieve"])
    assert result.exit_code == 0, result.output
    assert "cassette hits: 1" in result.output and "fixed" in result.output
