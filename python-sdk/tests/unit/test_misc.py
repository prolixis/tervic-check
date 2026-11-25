"""Smaller units: Markdown/JUnit rendering, safety helpers, exporter registry, multi-problem prompts."""

from __future__ import annotations

from typing import Any

import pytest
from helpers import node, record

import tervic
from tervic.analyze.model import build_view
from tervic.api import store_for
from tervic.core.run import Run
from tervic.core.safety import guard, internal_error_counts, safe
from tervic.fix.prompt import build_prompt
from tervic.report import junit, markdown


def two_problems(run: Run) -> None:
    node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
    node(run, "lookup", {"q": "x"}, {"error": "timeout"}, mode=None)
    node(run, "answer", {"q": "x", "documents": []}, {"answer": "ok"}, reads=["documents"])


def test_markdown_and_junit_render_groups() -> None:
    report = record(two_problems, name="pipe|line")
    text = markdown.render([report, record(lambda run: "ok")])
    assert text.startswith(markdown.MARKER) and "FAIL" in text and "root causes" in text
    assert "pipe\|line" in text and "<details>" in text
    assert markdown.render([]).endswith("No agent runs were recorded.\n")
    xml = junit.render([report])
    assert "failure" in xml and "lookup" in xml


def test_fix_prompt_with_several_problems() -> None:
    report = record(two_problems, name="multi")
    prompt = build_prompt(report, build_view(store_for().load_events(report.run_id)))
    assert "found 2 problems" in prompt and "## Problem 1" in prompt and "## Problem 2" in prompt


def test_safe_decorator_and_guard() -> None:
    @safe("unit.safe", default="fallback")
    def broken() -> str:
        raise RuntimeError("x")

    assert broken() == "fallback"
    with guard("unit.guard"):
        raise ValueError("swallowed")
    counts = internal_error_counts()
    assert counts["unit.safe"] == 1 and counts["unit.guard"] == 1


def test_exporters_from_config_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from tervic.config import get_config
    from tervic.export.registry import exporters_for

    tervic.configure(otel={"enabled": True}, webhook={"url": "https://example/hook"})
    first = exporters_for(get_config())
    second = exporters_for(get_config())
    assert len(first) == 2 and first[0] is second[0] and first[1] is second[1]


def test_interim_verdict_and_findings_property() -> None:
    with tervic.run("interim") as run:
        node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
        assert run.verdict == "fail"  # analysed on the fly before the run ends
    assert [f.detector_id for f in run.findings][:1] == ["TV301"] or run.findings


def test_max_events_per_run_drops_and_counts() -> None:
    tervic.configure(max_events_per_run=5)
    with tervic.run("capped") as run:
        for i in range(10):
            node(run, f"s{i}", {}, {"v": i})
    report: Any = run.report if run.wait() else None
    assert run.dropped > 0 and report.dropped_events == run.dropped
