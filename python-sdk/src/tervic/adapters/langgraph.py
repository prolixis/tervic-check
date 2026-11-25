"""LangGraph: ``app = tervic.attach(graph.compile())`` (``pip install tervic[langgraph]``).

Two mechanisms, both on the same compiled-graph instance (nothing global is patched):

* Each node's function (``app.nodes[name].bound.func`` / ``.afunc``) is wrapped, so every node run
  is a step with its exact input state and returned update. LangGraph decides which keyword
  arguments (``config``, ``runtime``, ...) to pass from the original function's signature before
  calling, so the ``(*args, **kwargs)`` wrapper receives exactly the same call.
* ``invoke`` / ``ainvoke`` / ``stream`` / ``astream`` open a run (unless one is already active) and add
  a LangChain callback handler to the config, which records LLM, tool and retriever calls made
  inside nodes.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import UUID

from tervic.core.introspect import reads_of, source_of
from tervic.core.run import (
    _MISSING,
    Run,
    current_run,
    current_step_id,
    pop_run,
    pop_step,
    push_run,
    push_step,
    replay_state,
)
from tervic.core.safety import report_internal_error

_ATTACHED = "__tervic_attached__"


def _update_of(result: Any) -> Any:
    """Node results may be a LangGraph ``Command``; its ``update`` is the state change."""
    if hasattr(result, "goto") and hasattr(result, "update") and type(result).__name__ == "Command":
        return result.update
    return result


_RETRIEVER_NAMES = ("retriev", "search_docs", "vector_search", "fetch_docs")


def node_kind(name: str, kinds: dict[str, str] | None) -> str:
    if kinds and name in kinds:
        return kinds[name]
    lowered = name.lower()
    return "retriever" if any(part in lowered for part in _RETRIEVER_NAMES) else "node"


def _wrap_node(
    name: str, original: Callable[..., Any], is_async: bool, kind: str = "node"
) -> Callable[..., Any]:
    source = source_of(original)

    def begin(args: tuple[Any, ...]) -> tuple[Run | None, str | None, bool, Any]:
        try:
            run = current_run()
            if run is None or not run.active:
                return None, None, False, None
            state = args[0] if args else None
            step_id = run.step_start(name, kind, state, source=source, reads=reads_of(original))
            replay = replay_state()
            if replay is not None and replay.before_from(name):
                found, value = replay.cassette.take("step", f"{name}#{run.occurrence(name)}")
                if found:
                    return run, step_id, True, value
                replay.cassette.live.append(f"step:{name}")
            return run, step_id, False, None
        except Exception as exc:
            report_internal_error("langgraph.node_begin", exc)
            return None, None, False, None

    def end(
        run: Run | None, step_id: str | None, result: Any, error: BaseException | None, exact: bool
    ) -> None:
        if run is None or step_id is None:
            return
        try:
            if error is not None:
                run.step_end(step_id, None, error=error)
            else:
                run.step_end(
                    step_id,
                    _update_of(result),
                    state_mode="update",
                    exact_output=result if exact else _MISSING,
                )
        except Exception as exc:
            report_internal_error("langgraph.node_end", exc)

    if is_async:

        @functools.wraps(original)
        async def async_node(*args: Any, **kwargs: Any) -> Any:
            run, step_id, served, value = begin(args)
            if served:
                end(run, step_id, value, None, False)
                return value
            token = push_step(step_id) if step_id else None
            try:
                result = await original(*args, **kwargs)
            except BaseException as exc:
                if token is not None:
                    pop_step(token)
                end(run, step_id, None, exc, False)
                raise
            if token is not None:
                pop_step(token)
            end(run, step_id, result, None, True)
            return result

        return async_node

    @functools.wraps(original)
    def sync_node(*args: Any, **kwargs: Any) -> Any:
        run, step_id, served, value = begin(args)
        if served:
            end(run, step_id, value, None, False)
            return value
        token = push_step(step_id) if step_id else None
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            if token is not None:
                pop_step(token)
            end(run, step_id, None, exc, False)
            raise
        if token is not None:
            pop_step(token)
        end(run, step_id, result, None, True)
        return result

    return sync_node


# ---------------------------------------------------------------- LangChain callbacks


def _make_handler_class() -> type:
    from langchain_core.callbacks import BaseCallbackHandler

    class TervicCallbackHandler(BaseCallbackHandler):  # type: ignore[misc,unused-ignore]
        """Records LLM, tool and retriever calls made inside graph nodes."""

        raise_error = False
        run_inline = True

        def __init__(self, run: Run) -> None:
            super().__init__()
            self.run = run
            self._llm: dict[UUID, dict[str, Any]] = {}
            self._tools: dict[UUID, dict[str, Any]] = {}
            self._retrievers: dict[UUID, str] = {}
            self._done: set[UUID] = set()

        # ---- LLMs
        def _llm_start(
            self,
            serialized: dict[str, Any] | None,
            messages: Any,
            run_id: UUID,
            metadata: dict[str, Any] | None,
            kwargs: dict[str, Any],
        ) -> None:
            try:
                if run_id in self._llm or run_id in self._done:
                    return
                params = kwargs.get("invocation_params") or {}
                meta = metadata or {}
                tools = params.get("tools") or []
                names = [
                    (t.get("function") or {}).get("name") or t.get("name")
                    for t in tools
                    if isinstance(t, dict)
                ]
                model = (
                    params.get("model")
                    or params.get("model_name")
                    or meta.get("ls_model_name")
                    or (serialized or {}).get("name")
                )
                self._llm[run_id] = {
                    "messages": messages,
                    "model": model,
                    "started": time.perf_counter(),
                    "provider": meta.get("ls_provider") or "langchain",
                    "tools": [n for n in names if n] if tools else None,
                    "step_id": current_step_id(),
                }
            except Exception as exc:
                report_internal_error("langgraph.llm_start", exc)

        def on_chat_model_start(
            self,
            serialized: dict[str, Any],
            messages: list[list[Any]],
            *,
            run_id: UUID,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            self._llm_start(serialized, messages[0] if messages else [], run_id, metadata, kwargs)

        def on_llm_start(
            self,
            serialized: dict[str, Any],
            prompts: list[str],
            *,
            run_id: UUID,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            self._llm_start(serialized, prompts, run_id, metadata, kwargs)

        def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
            try:
                start = self._llm.pop(run_id, None)
                if start is None or run_id in self._done:
                    return
                self._done.add(run_id)
                generation = (response.generations or [[None]])[0][0]
                message = getattr(generation, "message", None)
                text = getattr(generation, "text", "") or ""
                info = getattr(generation, "generation_info", None) or {}
                meta = getattr(message, "response_metadata", None) or {}
                usage = getattr(message, "usage_metadata", None) or {}
                llm_output = getattr(response, "llm_output", None) or {}
                token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
                calls = [
                    (c.get("name", ""), c.get("args")) for c in (getattr(message, "tool_calls", None) or [])
                ]
                self.run.llm_call(
                    provider=start["provider"],
                    model=meta.get("model_name") or start["model"],
                    input_messages=start["messages"],
                    output=text or getattr(message, "content", ""),
                    finish_reason=meta.get("finish_reason")
                    or meta.get("stop_reason")
                    or info.get("finish_reason"),
                    input_tokens=usage.get("input_tokens") or token_usage.get("prompt_tokens"),
                    output_tokens=usage.get("output_tokens") or token_usage.get("completion_tokens"),
                    latency_ms=(time.perf_counter() - start["started"]) * 1000,
                    tool_calls=calls,
                    available_tools=start["tools"],
                    refusal=meta.get("refusal") or getattr(message, "additional_kwargs", {}).get("refusal"),
                    step_id=start["step_id"],
                )
            except Exception as exc:
                report_internal_error("langgraph.llm_end", exc)

        def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
            self._llm.pop(run_id, None)

        # ---- tools
        def on_tool_start(
            self,
            serialized: dict[str, Any],
            input_str: str,
            *,
            run_id: UUID,
            inputs: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            try:
                self._tools[run_id] = {
                    "name": (serialized or {}).get("name") or kwargs.get("name") or "tool",
                    "args": inputs if inputs is not None else input_str,
                    "step_id": current_step_id(),
                }
            except Exception as exc:
                report_internal_error("langgraph.tool_start", exc)

        def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
            try:
                start = self._tools.pop(run_id, None)
                if start is not None:
                    result = getattr(output, "content", output)
                    self.run.tool_call(
                        start["name"], args=start["args"], result=result, step_id=start["step_id"]
                    )
            except Exception as exc:
                report_internal_error("langgraph.tool_end", exc)

        def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
            try:
                start = self._tools.pop(run_id, None)
                if start is not None:
                    self.run.tool_call(
                        start["name"], args=start["args"], error=error, step_id=start["step_id"]
                    )
            except Exception as exc:
                report_internal_error("langgraph.tool_error", exc)

        # ---- retrievers
        def on_retriever_start(
            self, serialized: dict[str, Any], query: str, *, run_id: UUID, **kwargs: Any
        ) -> None:
            try:
                name = (serialized or {}).get("name") or kwargs.get("name") or "retriever"
                self._retrievers[run_id] = self.run.step_start(
                    str(name),
                    "retriever",
                    {"query": query},
                    parent_step_id=current_step_id(),
                )
            except Exception as exc:
                report_internal_error("langgraph.retriever_start", exc)

        def on_retriever_end(self, documents: Any, *, run_id: UUID, **kwargs: Any) -> None:
            try:
                step_id = self._retrievers.pop(run_id, None)
                if step_id is not None:
                    self.run.step_end(step_id, list(documents))
            except Exception as exc:
                report_internal_error("langgraph.retriever_end", exc)

        def on_retriever_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
            try:
                step_id = self._retrievers.pop(run_id, None)
                if step_id is not None:
                    self.run.step_end(step_id, None, error=error)
            except Exception as exc:
                report_internal_error("langgraph.retriever_error", exc)

    return TervicCallbackHandler


_handler_class: type | None = None
_handlers: dict[str, Any] = {}


def make_handler(run: Run) -> Any:
    """One handler per run, so LangChain's handler de-duplication applies."""
    global _handler_class
    if _handler_class is None:
        _handler_class = _make_handler_class()
    handler = _handlers.get(run.run_id)
    if handler is None:
        handler = _handler_class(run)
        _handlers[run.run_id] = handler
        if len(_handlers) > 256:
            for key in list(_handlers)[:128]:
                _handlers.pop(key, None)
    return handler


def _with_handler(config: Any, handler: Any) -> Any:
    config = dict(config or {})
    callbacks = config.get("callbacks")
    if callbacks is None:
        config["callbacks"] = [handler]
    elif isinstance(callbacks, list):
        config["callbacks"] = [*callbacks, handler]
    else:  # a CallbackManager
        manager = callbacks.copy()
        manager.add_handler(handler, inherit=True)
        config["callbacks"] = manager
    return config


# ---------------------------------------------------------------- run entry points


class _Scope:
    def __init__(self, name: str, input: Any) -> None:
        self.owned = False
        self.token: Any = None
        self.run: Run | None = None
        try:
            existing = current_run()
            if existing is not None:
                self.run = existing
                return
            self.run = Run(name, framework="langgraph", input=input)
            self.owned = True
            self.token = push_run(self.run)
        except Exception as exc:
            report_internal_error("langgraph.scope_open", exc)

    def config(self, config: Any) -> Any:
        if self.run is None or not self.run.active:
            return config
        try:
            return _with_handler(config, make_handler(self.run))
        except Exception as exc:
            report_internal_error("langgraph.config", exc)
            return config

    def close(self, output: Any = None, error: BaseException | None = None) -> None:
        if not self.owned or self.run is None:
            return
        try:
            if self.token is not None:
                pop_run(self.token)
            if error is not None:
                self.run.end(status="crashed", error=error)
            else:
                self.run.end(status="completed", final_output=output)
        except Exception as exc:
            report_internal_error("langgraph.scope_close", exc)


def attach(app: Any, *, name: str | None = None, kinds: dict[str, str] | None = None) -> Any:
    """Instrument a compiled LangGraph graph in place and return the same object.

    ``kinds`` maps node names to step kinds (e.g. ``{"lookup": "retriever"}``); nodes whose name
    contains "retriev" are treated as retrievers automatically.
    """
    try:
        if getattr(app, _ATTACHED, False):
            return app
        nodes = getattr(app, "nodes", None)
        if not isinstance(nodes, dict) or not hasattr(app, "invoke"):
            report_internal_error(
                "langgraph.attach",
                TypeError(f"tervic.attach expects a compiled LangGraph graph, got {type(app).__qualname__}"),
            )
            return app
        for node_name, node in nodes.items():
            if node_name.startswith("__"):
                continue
            bound = getattr(node, "bound", None)
            if bound is None:
                continue
            func = getattr(bound, "func", None)
            afunc = getattr(bound, "afunc", None)
            kind = node_kind(node_name, kinds)
            if callable(func) and not getattr(func, "__tervic__", False):
                wrapped = _wrap_node(node_name, func, False, kind)
                wrapped.__tervic__ = True  # type: ignore[attr-defined]
                bound.func = wrapped
            if (
                callable(afunc)
                and inspect.iscoroutinefunction(afunc)
                and not getattr(afunc, "__tervic__", False)
            ):
                wrapped_a = _wrap_node(node_name, afunc, True, kind)
                wrapped_a.__tervic__ = True  # type: ignore[attr-defined]
                bound.afunc = wrapped_a
        run_name = name or getattr(app, "name", None) or "langgraph"
        if run_name == "LangGraph":
            run_name = "langgraph"
        _patch_entry_points(app, str(run_name))
        object.__setattr__(app, _ATTACHED, True)
    except Exception as exc:
        report_internal_error("langgraph.attach", exc)
    return app


def _patch_entry_points(app: Any, run_name: str) -> None:
    orig_invoke = app.invoke
    orig_ainvoke = app.ainvoke
    orig_stream = app.stream
    orig_astream = app.astream

    @functools.wraps(orig_invoke)
    def invoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
        scope = _Scope(run_name, input)
        try:
            result = orig_invoke(input, scope.config(config), **kwargs)
        except BaseException as exc:
            scope.close(error=exc)
            raise
        scope.close(output=result)
        return result

    @functools.wraps(orig_ainvoke)
    async def ainvoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
        scope = _Scope(run_name, input)
        try:
            result = await orig_ainvoke(input, scope.config(config), **kwargs)
        except BaseException as exc:
            scope.close(error=exc)
            raise
        scope.close(output=result)
        return result

    @functools.wraps(orig_stream)
    def stream(input: Any, config: Any = None, **kwargs: Any) -> Iterator[Any]:
        scope = _Scope(run_name, input)
        last: Any = None
        try:
            for chunk in orig_stream(input, scope.config(config), **kwargs):
                last = chunk
                yield chunk
        except GeneratorExit:
            scope.close(output=last)
            raise
        except BaseException as exc:
            scope.close(error=exc)
            raise
        scope.close(output=last)

    @functools.wraps(orig_astream)
    async def astream(input: Any, config: Any = None, **kwargs: Any) -> AsyncIterator[Any]:
        scope = _Scope(run_name, input)
        last: Any = None
        try:
            async for chunk in orig_astream(input, scope.config(config), **kwargs):
                last = chunk
                yield chunk
        except GeneratorExit:
            scope.close(output=last)
            raise
        except BaseException as exc:
            scope.close(error=exc)
            raise
        scope.close(output=last)

    for attr, fn in (("invoke", invoke), ("ainvoke", ainvoke), ("stream", stream), ("astream", astream)):
        object.__setattr__(app, attr, fn)
