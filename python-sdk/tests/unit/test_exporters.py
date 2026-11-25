from __future__ import annotations

import json
from typing import Any

import pytest
from helpers import node, record

import tervic
from tervic.export import Exporter
from tervic.export.webhook import WebhookExporter
from tervic.core.run import Run


def _failing(run: Run) -> None:
    node(run, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")


def test_custom_exporter_protocol() -> None:
    seen: list[Any] = []

    class Collect:
        def export(self, events: list[Any], report: Any) -> None:
            seen.append((len(events), report.verdict))

    exporter = Collect()
    assert isinstance(exporter, Exporter)
    tervic.add_exporter(exporter)
    try:
        record(_failing)
    finally:
        tervic.remove_exporter(exporter)
    assert seen and seen[0][1] == "fail"


@pytest.mark.parametrize(("fmt", "key"), [("json", "root_causes"), ("slack", "text")])
def test_webhook_payloads(fmt: str, key: str) -> None:
    posts: list[dict[str, Any]] = []
    hook = WebhookExporter("https://hooks.example/x", format=fmt,  # type: ignore[arg-type]
                           post=lambda url, body, timeout: posts.append(json.loads(body)))
    tervic.add_exporter(hook)
    try:
        record(_failing)
        record(lambda run: "fine")
    finally:
        tervic.remove_exporter(hook)
    assert len(posts) == 1 and key in posts[0]
    if fmt == "slack":
        assert "retrieve" in posts[0]["text"]


def test_webhook_on_policies() -> None:
    passing = record(lambda run: "fine")
    always = WebhookExporter("u", on="always", post=lambda *a: None)
    warn = WebhookExporter("u", on="warn", post=lambda *a: None)
    assert always.should_send(passing) and not warn.should_send(passing)


def test_webhook_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from tervic.export import webhook

    posts: list[str] = []
    monkeypatch.setattr(webhook, "_post", lambda url, body, timeout: posts.append(url))
    tervic.configure(webhook={"url": "https://hooks.example/cfg"})
    record(_failing)
    assert posts == ["https://hooks.example/cfg"]


def test_otel_exporter_emits_genai_spans_with_finding_events() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from tervic.export.otel import OtelExporter

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    exporter = OtelExporter(tracer_provider=provider)
    tervic.add_exporter(exporter)

    def body(run: Run) -> None:
        _failing(run)
        step = run.step_start("answer", "node", {"q": "x"})
        run.llm_call(model="gpt-4o-mini", provider="openai", input_messages=["x"], output="y",
                     input_tokens=10, output_tokens=2, finish_reason="stop", step_id=step)
        run.step_end(step, {"answer": "y"}, state_mode="update")

    try:
        record(body)
    finally:
        tervic.remove_exporter(exporter)
    spans = {s.name: s for s in memory.get_finished_spans()}
    root = next(s for name, s in spans.items() if name.startswith("invoke_workflow"))
    assert root.attributes["gen_ai.operation.name"] == "invoke_workflow"
    assert root.attributes["tervic.verdict"] == "fail"
    retrieval = spans["retrieval retrieve"]
    assert retrieval.attributes["gen_ai.operation.name"] == "retrieval"
    assert any(e.name == "tervic.finding" and e.attributes["tervic.detector_id"] == "TV301" for e in retrieval.events)
    chat = spans["chat gpt-4o-mini"]
    assert chat.attributes["gen_ai.usage.input_tokens"] == 10
    assert chat.attributes["gen_ai.request.model"] == "gpt-4o-mini"


def test_prod_mode_tail_sampling_keeps_failures_only() -> None:
    from tervic.api import store_for

    tervic.configure(mode="prod", sample_rate=0.0)
    with tervic.run("prod-ok") as ok:
        pass
    with tervic.run("prod-bad") as bad:
        node(bad, "retrieve", {"q": "x"}, {"documents": []}, kind="retriever")
    ok.wait()
    bad.wait()
    tervic.flush()
    names = {row.name for row in store_for().list_runs()}
    assert names == {"prod-bad"}
    assert ok.kept is False and bad.kept is True
    assert ok.record_cassette is False  # no cassette recording in prod by default
