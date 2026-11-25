"""OpenAI Agents SDK: ``tervic.attach_openai_agents()`` (``pip install tervic[openai-agents]``).

Registers a tracing processor with ``agents.add_trace_processor``. Each trace becomes a Tervic run
(or joins the active ``tervic.run(...)``); agent, tool (function), handoff and guardrail spans become
steps; generation/response spans become LLM calls. Tervic never changes span data.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from tervic.core.run import Run, current_run
from tervic.core.safety import report_internal_error

_registered: Any = None
_lock = threading.Lock()


def _parse(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return value
    return value


def _error(span: Any) -> tuple[str, str] | None:
    err = getattr(span, "error", None)
    if not err:
        return None
    if isinstance(err, dict):
        return (str(err.get("message", "SpanError")), json.dumps(err.get("data"), default=str)[:500])
    return ("SpanError", str(err))


def _make_processor_class() -> type:
    from agents.tracing import TracingProcessor

    class TervicTracingProcessor(TracingProcessor):  # type: ignore[misc,unused-ignore]
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self._runs: dict[str, tuple[Run, bool]] = {}
            self._steps: dict[str, str] = {}
            self._agent_tools: dict[str, Any] = {}
            self._last_text: dict[str, str] = {}
            self._parents: dict[str, str | None] = {}

        def _tools(self, span_id: str | None) -> list[str] | None:
            data = self._ancestor(span_id, self._agent_tools)
            tools = getattr(data, "tools", None)
            handoffs = getattr(data, "handoffs", None) or []
            if tools is None and not handoffs:
                return None
            names = list(tools or [])
            for target in handoffs:
                # Handoffs are exposed to the model as tools named transfer_to_<agent>.
                names += [str(target), "transfer_to_" + str(target).lower().replace(" ", "_").replace("-", "_")]
            return names

        def _ancestor(self, span_id: str | None, table: dict[str, Any]) -> Any:
            """Nearest ancestor span (including itself) found in ``table``; spans such as turn
            spans sit between an agent span and its tool spans."""
            seen = 0
            while span_id and seen < 50:
                if span_id in table:
                    return table[span_id]
                span_id = self._parents.get(span_id)
                seen += 1
            return None

        # ---------------------------------------------------------- traces
        def on_trace_start(self, trace: Any) -> None:
            try:
                existing = current_run()
                if existing is not None:
                    run, owned = existing, False
                else:
                    run, owned = (
                        Run(getattr(trace, "name", None) or "openai-agents", framework="openai-agents"),
                        True,
                    )
                with self._lock:
                    self._runs[trace.trace_id] = (run, owned)
            except Exception as exc:
                report_internal_error("openai_agents.trace_start", exc)

        def on_trace_end(self, trace: Any) -> None:
            try:
                with self._lock:
                    entry = self._runs.pop(trace.trace_id, None)
                    text = self._last_text.pop(trace.trace_id, None)
                if entry is None:
                    return
                run, owned = entry
                if owned:
                    run.end(status="completed", final_output=text)
                elif text is not None:
                    run.set_output(text)
            except Exception as exc:
                report_internal_error("openai_agents.trace_end", exc)

        # ---------------------------------------------------------- spans
        def _run(self, span: Any) -> Run | None:
            with self._lock:
                entry = self._runs.get(span.trace_id)
            return entry[0] if entry else None

        def on_span_start(self, span: Any) -> None:
            try:
                self._parents[span.span_id] = span.parent_id
                run = self._run(span)
                if run is None:
                    return
                data = span.span_data
                kind = getattr(data, "type", "")
                parent = self._ancestor(span.parent_id, self._steps)
                if kind == "agent":
                    step_id = run.step_start(
                        data.name,
                        "agent",
                        {"tools": data.tools, "handoffs": data.handoffs},
                        parent_step_id=parent,
                    )
                    self._steps[span.span_id] = step_id
                    self._agent_tools[span.span_id] = data
                elif kind == "handoff":
                    self._steps[span.span_id] = run.step_start(
                        f"handoff:{data.to_agent}",
                        "handoff",
                        {"from": data.from_agent, "to": data.to_agent},
                        parent_step_id=parent,
                    )
                elif kind in ("guardrail", "custom", "mcp_tools"):
                    self._steps[span.span_id] = run.step_start(
                        str(getattr(data, "name", kind)),
                        "custom",
                        None,
                        parent_step_id=parent,
                    )
            except Exception as exc:
                report_internal_error("openai_agents.span_start", exc)

        def on_span_end(self, span: Any) -> None:
            try:
                run = self._run(span)
                if run is None:
                    return
                data = span.span_data
                kind = getattr(data, "type", "")
                parent = self._ancestor(span.parent_id, self._steps)
                error = _error(span)
                if kind == "function":
                    tools = self._tools(span.parent_id)
                    args = _parse(data.input)
                    result = _parse(data.output)
                    step_id = run.step_start(data.name, "tool", args, parent_step_id=parent)
                    run.tool_call(
                        data.name,
                        args=args,
                        result=result,
                        error=error,
                        available_tools=tools,
                        step_id=step_id,
                    )
                    run.step_end(step_id, result, error=error)
                elif kind == "response":
                    self._response(run, span, parent)
                elif kind == "generation":
                    self._generation(run, span, parent)
                elif kind in ("agent", "handoff", "guardrail", "custom", "mcp_tools"):
                    ended = self._steps.pop(span.span_id) if span.span_id in self._steps else None
                    if ended is not None:
                        output = None
                        if kind == "guardrail":
                            output = {"triggered": getattr(data, "triggered", None)}
                        run.step_end(ended, output, error=error)
            except Exception as exc:
                report_internal_error("openai_agents.span_end", exc)

        def _response(self, run: Run, span: Any, parent: str | None) -> None:
            from tervic.adapters.llm_clients import parse_openai_responses

            data = span.span_data
            response = getattr(data, "response", None)
            parsed = parse_openai_responses(response) if response is not None else {}
            tools = [getattr(t, "name", None) for t in (getattr(response, "tools", None) or [])]
            tool_names = [t for t in tools if t] or self._tools(span.parent_id)
            usage = getattr(data, "usage", None) or {}
            run.llm_call(
                provider="openai",
                model=parsed.get("model"),
                input_messages=getattr(data, "input", None),
                output=parsed.get("output"),
                finish_reason=parsed.get("finish_reason"),
                input_tokens=parsed.get("input_tokens") or usage.get("input_tokens"),
                output_tokens=parsed.get("output_tokens") or usage.get("output_tokens"),
                tool_calls=parsed.get("tool_calls") or [],
                available_tools=tool_names,
                refusal=parsed.get("refusal"),
                step_id=parent,
            )
            if parsed.get("output"):
                with self._lock:
                    self._last_text[span.trace_id] = str(parsed["output"])

        def _generation(self, run: Run, span: Any, parent: str | None) -> None:
            data = span.span_data
            output = list(data.output or [])
            texts: list[str] = []
            calls: list[tuple[str, Any]] = []
            finish = None
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    texts.extend(str(p.get("text", "")) for p in content if isinstance(p, dict))
                for call in item.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    calls.append((str(fn.get("name", "")), fn.get("arguments")))
                finish = item.get("finish_reason") or finish
            usage = data.usage or {}
            tools = [
                str((t.get("function") or {}).get("name") or t.get("name"))
                for t in (data.tools or [])
                if isinstance(t, dict)
            ]
            run.llm_call(
                provider="openai",
                model=data.model,
                input_messages=list(data.input or []),
                output="".join(texts),
                finish_reason=finish,
                input_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
                output_tokens=usage.get("output_tokens") or usage.get("completion_tokens"),
                tool_calls=calls,
                available_tools=tools or self._tools(span.parent_id),
                step_id=parent,
            )
            if texts:
                with self._lock:
                    self._last_text[span.trace_id] = "".join(texts)

        def shutdown(self) -> None:
            return None

        def force_flush(self) -> None:
            from tervic.api import flush

            flush()

    return TervicTracingProcessor


def attach_openai_agents(*, replace_default: bool = False) -> Any:
    """Register Tervic as an OpenAI Agents SDK trace processor. Safe to call more than once.

    ``replace_default=True`` makes Tervic the only processor (the SDK then stops uploading traces
    to OpenAI), which keeps everything local.
    """
    global _registered
    with _lock:
        if _registered is not None:
            return _registered
        try:
            import agents

            processor = _make_processor_class()()
            if replace_default:
                agents.set_trace_processors([processor])
            else:
                agents.add_trace_processor(processor)
            _registered = processor
        except Exception as exc:
            report_internal_error("openai_agents.attach", exc)
        return _registered
