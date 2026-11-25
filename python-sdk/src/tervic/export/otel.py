"""OpenTelemetry exporter (``pip install tervic[otel]``).

Emits one ``invoke_workflow`` span per run, a child span per step (``invoke_agent``/``execute_tool``/
``retrieval``) and a ``chat`` span per LLM call, using the OTel GenAI semantic-convention attribute
names. Tervic findings are attached as span events named ``tervic.finding``. Spans go to whatever
TracerProvider the application configured (Langfuse, Datadog, Grafana, Honeycomb, ...).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tervic.analyze.engine import Report
    from tervic.core.events import Event

OPERATION_BY_KIND = {
    "tool": "execute_tool",
    "retriever": "retrieval",
    "llm": "chat",
    "agent": "invoke_agent",
    "handoff": "invoke_agent",
    "node": "invoke_agent",
    "custom": "invoke_agent",
}


def _ns(ts: float) -> int:
    return int(ts * 1_000_000_000)


class OtelExporter:
    def __init__(self, tracer_provider: Any = None, service_name: str = "tervic") -> None:
        from opentelemetry import trace

        provider = tracer_provider or trace.get_tracer_provider()
        self._trace = trace
        self.tracer = provider.get_tracer("tervic", schema_url="https://opentelemetry.io/schemas/1.37.0")
        self.service_name = service_name

    def export(self, events: list[Event], report: Report) -> None:
        from tervic.analyze.model import build_view

        view = build_view(events)
        trace = self._trace
        start = _ns(view.started_ts or (events[0].ts if events else 0.0))
        end = start + int(view.duration_ms * 1_000_000)
        root = self.tracer.start_span(
            f"invoke_workflow {view.name}",
            start_time=start,
            attributes={
                "gen_ai.operation.name": "invoke_workflow",
                "gen_ai.agent.name": view.name,
                "tervic.run_id": view.run_id,
                "tervic.framework": view.framework,
                "tervic.verdict": report.verdict,
                "tervic.findings.error": report.counts.get("error", 0),
                "tervic.findings.warn": report.counts.get("warn", 0),
                "service.name": self.service_name,
            },
        )
        by_step: dict[str, list[Any]] = {}
        for finding in report.findings:
            by_step.setdefault(finding.step_id or "", []).append(finding)
        spans: dict[str, Any] = {}
        root_ctx = trace.set_span_in_context(root)
        for step in view.steps:
            parent = spans.get(step.parent_step_id or "")
            ctx = trace.set_span_in_context(parent) if parent is not None else root_ctx
            op = OPERATION_BY_KIND.get(step.kind, "invoke_agent")
            attrs: dict[str, Any] = {
                "gen_ai.operation.name": op,
                "tervic.step_id": step.step_id,
                "tervic.step.kind": step.kind,
            }
            if op == "execute_tool":
                attrs["gen_ai.tool.name"] = step.name
            else:
                attrs["gen_ai.agent.name"] = step.name
            if step.source:
                attrs["code.location"] = step.source
            span = self.tracer.start_span(
                f"{op} {step.name}", context=ctx, start_time=_ns(step.started_ts), attributes=attrs
            )
            spans[step.step_id] = span
            for call in step.llm_calls:
                self._llm_span(call, trace.set_span_in_context(span))
            for finding in by_step.get(step.step_id, []):
                span.add_event("tervic.finding", attributes=_finding_attrs(finding))
            if step.error is not None:
                span.set_status(
                    trace.Status(trace.StatusCode.ERROR, f"{step.error.type}: {step.error.message}")
                )
            span.end(end_time=_ns(step.ended_ts or step.started_ts))
        for call in view.llm_calls:
            if call.step_id not in spans:
                self._llm_span(call, root_ctx)
        for finding in by_step.get("", []):
            root.add_event("tervic.finding", attributes=_finding_attrs(finding))
        if report.verdict == "fail":
            root.set_status(trace.Status(trace.StatusCode.ERROR, "tervic verdict: fail"))
        root.end(end_time=end)

    def _llm_span(self, call: Any, ctx: Any) -> None:
        attrs: dict[str, Any] = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": call.provider}
        if call.model:
            attrs["gen_ai.request.model"] = call.model
            attrs["gen_ai.response.model"] = call.model
        if call.input_tokens is not None:
            attrs["gen_ai.usage.input_tokens"] = call.input_tokens
        if call.output_tokens is not None:
            attrs["gen_ai.usage.output_tokens"] = call.output_tokens
        if call.finish_reason:
            attrs["gen_ai.response.finish_reasons"] = [call.finish_reason]
        if call.cost_usd is not None:
            attrs["tervic.cost_usd"] = call.cost_usd
        start = _ns(call.ts - (call.latency_ms or 0) / 1000)
        span = self.tracer.start_span(
            f"chat {call.model or ''}".strip(), context=ctx, start_time=start, attributes=attrs
        )
        span.end(end_time=_ns(call.ts))


def _finding_attrs(finding: Any) -> dict[str, Any]:
    attrs = {
        "tervic.detector_id": finding.detector_id,
        "tervic.detector": finding.name,
        "tervic.severity": finding.severity,
        "tervic.message": finding.message,
    }
    if finding.field_path:
        attrs["tervic.field"] = finding.field_path
    if finding.root_cause_step_name:
        attrs["tervic.root_cause_step"] = finding.root_cause_step_name
    return attrs
