from __future__ import annotations

import re
from typing import Any

from helpers import node, record

import tervic
from tervic.analyze import judge
from tervic.analyze.model import build_view
from tervic.api import store_for
from tervic.core.run import Run
from tervic.fix.prompt import build_prompt

EXPECTED = """\
Tervic found a silent failure in run <ID> (rag).

Root cause: step `retrieve` (src/agent/nodes.py:42): Retriever `retrieve` returned 0 documents.
Effect: `answer` ran on the bad value: `answer` reads `documents`... (TV301, TV302, TV003).
Evidence: input query='what is tervic?', output documents=[] (0 items). Baseline run had 5 documents.

Please:
1. Open src/agent/nodes.py:42 and find why `documents` can be empty.
2. Fix the cause (do not just add a fallback that hides it).
3. If an empty result is legitimate, raise or return an explicit "no results" state the next step handles.
4. Re-run with: tervic replay <ID> --from retrieve
"""


def _rag(documents: list[str]) -> Any:
    def body(run: Run) -> str:
        step = run.step_start("retrieve", "retriever", {"query": "what is tervic?"}, source="src/agent/nodes.py:42")
        run.step_end(step, {"documents": documents}, state_mode="update")
        answer = run.step_start("answer", "node", {"query": "what is tervic?", "documents": documents},
                                reads=["documents"])
        run.llm_call(model="m", input_messages=["Context: " + " ".join(documents)], output="x", step_id=answer)
        run.step_end(answer, {"answer": "x"}, state_mode="update")
        return "x"

    return record(body, name="rag")


def test_fix_prompt_snapshot() -> None:
    good = _rag(["d1 about tervic", "d2", "d3", "d4", "d5"])
    store_for().save_baseline("rag", good.run_id)
    bad = _rag([])
    store = store_for()
    prompt = build_prompt(bad, build_view(store.load_events(bad.run_id)),
                          build_view(store.baseline_events("rag") or []))
    normalized = prompt.replace(bad.run_id, "<ID>")
    normalized = re.sub(r"(Effect: `answer` ran on the bad value: `answer` reads `documents`)[^(]*", r"\1... ",
                        normalized)
    normalized = normalized.replace("... (", "... (").replace("documents`...  (", "documents`... (")
    lines = normalized.splitlines()
    expected = EXPECTED.splitlines()
    assert lines[0] == expected[0]
    assert lines[2] == expected[2]
    assert lines[3].startswith("Effect:") and "TV301" in lines[3] and "TV302" in lines[3]
    assert lines[4] == expected[4]
    assert lines[6:] == expected[6:]


def test_fix_prompt_formats_and_no_findings() -> None:
    clean = record(lambda run: "fine", name="clean")
    view = build_view(store_for().load_events(clean.run_id))
    assert "Nothing to fix" in build_prompt(clean, view)
    bad = _rag([])
    view = build_view(store_for().load_events(bad.run_id))
    assert "get_findings" in build_prompt(bad, view, fmt="claude")
    assert "Open @src/agent/nodes.py (line 42)" in build_prompt(bad, view, fmt="cursor")


def test_judge_off_by_default_sends_nothing() -> None:
    calls: list[Any] = []
    judge.set_transport(lambda url, headers, body: calls.append(url) or {})
    try:
        record(lambda run: node(run, "a", {"q": 1}, {}) and "x", input="question")
    finally:
        judge.set_transport(None)
    assert calls == []


def test_judge_flags_bad_answer_with_caps(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    sent: list[dict[str, Any]] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {"content": [{"type": "text", "text": '{"ok": false, "reason": "does not answer"}'}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}

    judge.set_transport(transport)
    tervic.configure(judge={"enabled": True, "max_calls": 1})
    try:
        report = record(lambda run: node(run, "plan", {"q": 1}, {}) and "unrelated answer",
                        input="what is tervic? sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234")
    finally:
        judge.set_transport(None)
    assert len(sent) == 1  # capped
    assert "sk-proj" not in str(sent[0])  # only redacted snapshots leave the machine
    flagged = [f for f in report.findings if f.detector_id == "TV801"]
    assert flagged and "does not answer" in flagged[0].message


def test_judge_without_key_is_skipped(monkeypatch: Any) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(judge, "get_key", lambda provider: None)
    tervic.configure(judge={"enabled": True})
    report = record(lambda run: "x", input="q")
    assert report.judge and "no anthropic key" in report.judge[0]["skipped"]


def test_judge_openai_and_gemini_parsing(monkeypatch: Any) -> None:
    monkeypatch.setattr(judge, "get_key", lambda provider: "k")
    judge.set_transport(lambda url, headers, body: (
        {"choices": [{"message": {"content": '{"ok": true, "reason": "fine"}'}}], "usage": {}}
        if "openai" in url else
        {"candidates": [{"content": {"parts": [{"text": "not json"}]}}], "usageMetadata": {}}
    ))
    try:
        tervic.configure(judge={"enabled": True, "provider": "openai"})
        assert record(lambda run: "x", input="q").judge[0]["ok"] is True
        tervic.configure(judge={"enabled": True, "provider": "gemini"})
        assert "unparseable" in record(lambda run: "x", input="q").judge[0]["reason"]
    finally:
        judge.set_transport(None)
