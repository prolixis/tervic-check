"""Optional LLM judge (off by default; uses the user's own key).

It only judges (1) whether the final answer addresses the question and (2) whether a step flagged
``warn`` produced a reasonable output for its input. It sends redacted snapshots only, makes at
most ``judge.max_calls`` calls per run and stops once ``judge.max_cost_usd`` is spent. When the
judge is disabled nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from typing import Any

from tervic.analyze.findings import Finding, preview
from tervic.analyze.model import RunView
from tervic.config import TervicConfig
from tervic.core.pricing import estimate_cost

KEYRING_SERVICE = "tervic"
ENV_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-4-5", "gemini": "gemini-2.5-flash"}

Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


def _http(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return result


_transport: Transport = _http


def set_transport(transport: Transport | None) -> None:
    """Replace the HTTP transport (used by tests)."""
    global _transport
    _transport = transport or _http


def get_key(provider: str) -> str | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, provider)
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(ENV_KEYS.get(provider, ""), None) or None


def set_key(provider: str, key: str) -> str:
    """Store a key in the OS keyring. Returns where it was stored."""
    import keyring

    keyring.set_password(KEYRING_SERVICE, provider, key)
    return "OS keyring"


def _ask(provider: str, model: str, key: str, prompt: str) -> tuple[str, int, int]:
    if provider == "openai":
        data = _transport(
            "https://api.openai.com/v1/chat/completions",
            {"authorization": f"Bearer {key}"},
            {
                "model": model,
                "max_tokens": 200,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        usage = data.get("usage", {})
        return (
            str(data["choices"][0]["message"]["content"]),
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )
    if provider == "anthropic":
        data = _transport(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]},
        )
        usage = data.get("usage", {})
        text = "".join(block.get("text", "") for block in data.get("content", []))
        return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    data = _transport(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": key},
        {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 200}},
    )
    usage = data.get("usageMetadata", {})
    parts = data["candidates"][0]["content"]["parts"]
    return (
        "".join(p.get("text", "") for p in parts),
        int(usage.get("promptTokenCount", 0)),
        int(usage.get("candidatesTokenCount", 0)),
    )


_INSTRUCTIONS = (
    "You check AI agent runs for silent failures. Reply with JSON only: "
    '{"ok": true|false, "reason": "<one short sentence>"}.\n\n'
)


def judge_run(
    run: RunView, findings: list[Finding], config: TervicConfig
) -> tuple[list[Finding], list[dict[str, Any]]]:
    provider = config.judge.provider
    key = get_key(provider)
    if not key:
        return [], [{"skipped": f"no {provider} key (run `tervic key set --provider {provider}`)"}]
    model = config.judge.model or DEFAULT_MODELS[provider]
    questions: list[tuple[str | None, str]] = []
    if run.input is not None:
        questions.append(
            (
                None,
                _INSTRUCTIONS + "Does the final answer address the question?\n"
                f"QUESTION/INPUT: {preview(run.input, 1500)}\n"
                f"FINAL ANSWER: {preview(run.effective_final_output, 2500)}",
            )
        )
    warned = {f.step_id for f in findings if f.severity == "warn" and f.step_id}
    for step in run.steps:
        if step.step_id in warned:
            questions.append(
                (
                    step.step_id,
                    _INSTRUCTIONS + f"Is the output of step '{step.name}' "
                    "reasonable given its input?\n"
                    f"INPUT: {preview(step.input, 1500)}\nOUTPUT: {preview(step.output, 1500)}",
                )
            )
    results: list[dict[str, Any]] = []
    out: list[Finding] = []
    spent = 0.0
    for step_id, prompt in questions[: config.judge.max_calls]:
        if spent >= config.judge.max_cost_usd:
            results.append({"skipped": "judge cost cap reached"})
            break
        text, tokens_in, tokens_out = _ask(provider, model, key, prompt)
        spent += estimate_cost(model, tokens_in, tokens_out) or 0.0
        verdict = _parse(text)
        judged = run.step(step_id)
        results.append({"step": judged.name if judged else None, **verdict})
        if verdict.get("ok") is False:
            out.append(
                Finding(
                    detector_id="TV801",
                    name="judge-flag",
                    severity="warn",
                    step_id=step_id,
                    step_name=judged.name if judged else None,
                    message=f"LLM judge: {verdict.get('reason', 'output looks wrong')}",
                    evidence={"model": model},
                    source=judged.source if judged else None,
                    how_to_fix="Review the step's output; the judge is advisory and never fails a run on its own unless fail_on = 'warn'.",
                )
            )
    return out, results


def _parse(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return {"ok": bool(data.get("ok", True)), "reason": str(data.get("reason", ""))}
        except json.JSONDecodeError:
            pass
    return {"ok": True, "reason": f"unparseable judge reply: {text[:80]}"}
