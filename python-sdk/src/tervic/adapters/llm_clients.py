"""``tervic.attach_llm(client)``: record LLM calls made with openai, anthropic or google-genai clients.

The client's methods are wrapped on that instance only. Return values and exceptions are unchanged;
Tervic reads the response after the call returns. During ``tervic replay`` the recorded response is
returned instead of calling the provider (no cost).
"""

from __future__ import annotations

import functools
import inspect
import json
import time
from collections.abc import Callable
from typing import Any

from tervic.core.run import Run, current_run, replay_state
from tervic.core.safety import report_internal_error
from tervic.store.cassette import request_key


class ReplayMiss(RuntimeError):
    """Raised during ``tervic replay --offline`` when a call has no recorded response."""


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if obj is None:
            return default
        obj = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return default if obj is None else obj


def _tool_names(tools: Any) -> list[str] | None:
    if not tools:
        return None
    names: list[str] = []
    for tool in tools:
        name = (
            _get(tool, "function", "name")
            or _get(tool, "name")
            or [d.get("name") for d in _get(tool, "function_declarations", default=[]) if isinstance(d, dict)]
        )
        if isinstance(name, list):
            names.extend(str(n) for n in name if n)
        elif name:
            names.append(str(name))
    return names or None


# ---------------------------------------------------------------- response parsers


def parse_openai_chat(resp: Any) -> dict[str, Any]:
    choice = (_get(resp, "choices") or [None])[0]
    message = _get(choice, "message")
    calls = [
        (str(_get(tc, "function", "name", default="")), _get(tc, "function", "arguments"))
        for tc in (_get(message, "tool_calls") or [])
    ]
    return {
        "output": _get(message, "content", default=""),
        "finish_reason": _get(choice, "finish_reason"),
        "input_tokens": _get(resp, "usage", "prompt_tokens"),
        "output_tokens": _get(resp, "usage", "completion_tokens"),
        "tool_calls": calls,
        "refusal": _get(message, "refusal"),
        "model": _get(resp, "model"),
    }


def parse_openai_responses(resp: Any) -> dict[str, Any]:
    texts: list[str] = []
    calls: list[tuple[str, Any]] = []
    refusal = None
    for item in _get(resp, "output") or []:
        kind = _get(item, "type")
        if kind == "function_call":
            calls.append((str(_get(item, "name", default="")), _get(item, "arguments")))
        elif kind == "message":
            for part in _get(item, "content") or []:
                if _get(part, "type") == "output_text":
                    texts.append(str(_get(part, "text", default="")))
                elif _get(part, "type") == "refusal":
                    refusal = _get(part, "refusal")
    finish = _get(resp, "incomplete_details", "reason") or _get(resp, "status")
    return {
        "output": "".join(texts),
        "finish_reason": finish,
        "input_tokens": _get(resp, "usage", "input_tokens"),
        "output_tokens": _get(resp, "usage", "output_tokens"),
        "tool_calls": calls,
        "refusal": refusal,
        "model": _get(resp, "model"),
    }


def parse_anthropic(resp: Any) -> dict[str, Any]:
    texts: list[str] = []
    calls: list[tuple[str, Any]] = []
    for block in _get(resp, "content") or []:
        kind = _get(block, "type")
        if kind == "text":
            texts.append(str(_get(block, "text", default="")))
        elif kind == "tool_use":
            calls.append((str(_get(block, "name", default="")), _get(block, "input")))
    stop = _get(resp, "stop_reason")
    return {
        "output": "".join(texts),
        "finish_reason": stop,
        "input_tokens": _get(resp, "usage", "input_tokens"),
        "output_tokens": _get(resp, "usage", "output_tokens"),
        "tool_calls": calls,
        "refusal": "refused by provider" if stop == "refusal" else None,
        "model": _get(resp, "model"),
    }


def parse_gemini(resp: Any) -> dict[str, Any]:
    candidate = (_get(resp, "candidates") or [None])[0]
    finish = _get(candidate, "finish_reason")
    finish_name = getattr(finish, "name", finish)
    texts: list[str] = []
    calls: list[tuple[str, Any]] = []
    for part in _get(candidate, "content", "parts") or []:
        if _get(part, "text"):
            texts.append(str(_get(part, "text")))
        call = _get(part, "function_call")
        if call is not None:
            calls.append((str(_get(call, "name", default="")), _get(call, "args")))
    finish_text = str(finish_name).lower() if finish_name else None
    return {
        "output": "".join(texts),
        "finish_reason": "length" if finish_text == "max_tokens" else finish_text,
        "input_tokens": _get(resp, "usage_metadata", "prompt_token_count"),
        "output_tokens": _get(resp, "usage_metadata", "candidates_token_count"),
        "tool_calls": calls,
        "refusal": "blocked by provider safety filter"
        if finish_text in ("safety", "prohibited_content")
        else None,
        "model": _get(resp, "model_version"),
    }


# ---------------------------------------------------------------- wrapping


def _record(
    run: Run, provider: str, kwargs: dict[str, Any], parsed: dict[str, Any], latency_ms: float, input_key: str
) -> None:
    messages = kwargs.get("messages", kwargs.get("input", kwargs.get("contents")))
    system = kwargs.get("system") or kwargs.get("instructions")
    if system is not None:
        messages = {"system": system, "messages": messages}
    run.llm_call(
        provider=provider,
        model=parsed.get("model") or kwargs.get("model"),
        input_messages=messages,
        output=parsed.get("output"),
        finish_reason=parsed.get("finish_reason"),
        input_tokens=parsed.get("input_tokens"),
        output_tokens=parsed.get("output_tokens"),
        latency_ms=latency_ms,
        tool_calls=parsed.get("tool_calls") or [],
        available_tools=_tool_names(kwargs.get("tools") or _get(kwargs.get("config"), "tools")),
        refusal=parsed.get("refusal"),
    )


def _key(provider: str, kwargs: dict[str, Any]) -> str:
    return request_key(
        provider,
        kwargs.get("model"),
        kwargs.get("messages"),
        kwargs.get("input"),
        kwargs.get("contents"),
        kwargs.get("system"),
        kwargs.get("instructions"),
    )


def _wrap_method(owner: Any, attr: str, provider: str, parser: Callable[[Any], dict[str, Any]]) -> bool:
    original = getattr(owner, attr, None)
    if original is None or getattr(original, "__tervic_wrapped__", False):
        return False

    def before(kwargs: dict[str, Any]) -> tuple[Run | None, str, tuple[bool, Any]]:
        try:
            run = current_run()
            key = _key(provider, kwargs)
            replay = replay_state()
            if run is not None and replay is not None and replay.serve_llm():
                found, value = replay.cassette.take("llm", key)
                if found:
                    return run, key, (True, value)
                if replay.offline:
                    return (
                        run,
                        key,
                        (
                            False,
                            ReplayMiss(
                                f"tervic replay --offline: no recorded {provider} response for this call"
                            ),
                        ),
                    )
            return run, key, (False, None)
        except Exception as exc:
            report_internal_error("llm.before", exc)
            return None, "", (False, None)

    def after(
        run: Run | None, key: str, kwargs: dict[str, Any], resp: Any, started: float, served: bool
    ) -> None:
        if run is None:
            return
        try:
            if kwargs.get("stream"):
                run.llm_call(
                    provider=provider,
                    model=kwargs.get("model"),
                    input_messages=kwargs.get("messages", kwargs.get("input")),
                    output=None,
                    finish_reason="stream",
                )
                return
            parsed = parser(resp)
            _record(run, provider, kwargs, parsed, (time.perf_counter() - started) * 1000, key)
            if not served:
                run.record_llm_response(key, resp)
        except Exception as exc:
            report_internal_error("llm.after", exc)

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            run, key, (served, value) = before(kwargs)
            if isinstance(value, ReplayMiss):
                raise value
            started = time.perf_counter()
            resp = value if served else await original(*args, **kwargs)
            after(run, key, kwargs, resp, started, served)
            return resp

        wrapped: Any = async_wrapped
    else:

        @functools.wraps(original)
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            run, key, (served, value) = before(kwargs)
            if isinstance(value, ReplayMiss):
                raise value
            started = time.perf_counter()
            resp = value if served else original(*args, **kwargs)
            after(run, key, kwargs, resp, started, served)
            return resp

        wrapped = sync_wrapped
    wrapped.__tervic_wrapped__ = True
    setattr(owner, attr, wrapped)
    return True


def attach_llm(client: Any) -> Any:
    """Record calls made with an ``openai``, ``anthropic`` or ``google-genai`` client. Returns it."""
    try:
        module = type(client).__module__.split(".")[0]
        wrapped = False
        if module == "openai" or (
            hasattr(client, "chat") and hasattr(getattr(client, "chat", None), "completions")
        ):
            chat = getattr(getattr(client, "chat", None), "completions", None)
            if chat is not None:
                wrapped |= _wrap_method(chat, "create", "openai", parse_openai_chat)
            responses = getattr(client, "responses", None)
            if responses is not None and hasattr(responses, "create"):
                wrapped |= _wrap_method(responses, "create", "openai", parse_openai_responses)
        if module == "anthropic" or (hasattr(client, "messages") and hasattr(client.messages, "create")):
            messages = getattr(client, "messages", None)
            if messages is not None:
                wrapped |= _wrap_method(messages, "create", "anthropic", parse_anthropic)
        if module == "google" or (
            hasattr(client, "models") and hasattr(getattr(client, "models", None), "generate_content")
        ):
            models = getattr(client, "models", None)
            if models is not None:
                wrapped |= _wrap_method(models, "generate_content", "gemini", parse_gemini)
            aio_models = getattr(getattr(client, "aio", None), "models", None)
            if aio_models is not None:
                wrapped |= _wrap_method(aio_models, "generate_content", "gemini", parse_gemini)
        if not wrapped:
            report_internal_error(
                "attach_llm",
                TypeError(
                    f"{type(client).__qualname__} is not a recognized openai/anthropic/google-genai client"
                ),
            )
    except Exception as exc:
        report_internal_error("attach_llm", exc)
    return client


def json_or_text(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value
