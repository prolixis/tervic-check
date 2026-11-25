"""attach_llm with real openai / anthropic response types and a google-genai shaped fake (no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from helpers import ids

import tervic
from tervic.adapters.llm_clients import ReplayMiss, parse_gemini
from tervic.analyze.model import build_view
from tervic.api import store_for
from tervic.core.run import ReplayState, set_replay_state
from tervic.store.cassette import Cassette

openai_types = pytest.importorskip("openai.types.chat")
anthropic_types = pytest.importorskip("anthropic.types")


def chat_completion(text: str, finish: str = "stop", tool: tuple[str, str] | None = None) -> Any:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool:
        message["tool_calls"] = [{"id": "c1", "type": "function", "function": {"name": tool[0], "arguments": tool[1]}}]
    return openai_types.ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 1, "model": "gpt-4o-mini",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    })


class FakeOpenAI:
    """Same shape as openai.OpenAI for the parts Tervic touches."""

    def __init__(self, responses: list[Any]) -> None:
        self.calls = 0
        outer = self

        class Completions:
            def create(self, **kwargs: Any) -> Any:
                outer.calls += 1
                return responses[min(outer.calls - 1, len(responses) - 1)]

        self.chat = SimpleNamespace(completions=Completions())


class FakeAnthropic:
    def __init__(self, response: Any) -> None:
        class Messages:
            def create(self, **kwargs: Any) -> Any:
                return response

        self.messages = Messages()


class FakeGemini:
    def __init__(self, response: Any) -> None:
        class Models:
            def generate_content(self, **kwargs: Any) -> Any:
                return response

        self.models = Models()


def _llm_calls(run: Any) -> list[Any]:
    tervic.flush()
    return build_view(store_for().load_events(run.run_id)).llm_calls


def test_openai_chat_recorded_without_changing_return_value() -> None:
    response = chat_completion("Paris.")
    client = tervic.attach_llm(FakeOpenAI([response]))
    with tervic.run("oa") as run:
        out = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "Capital?"}])
    assert out is response
    call = _llm_calls(run)[0]
    assert call.provider == "openai" and call.model == "gpt-4o-mini" and call.output == "Paris."
    assert call.input_tokens == 100 and call.output_tokens == 20 and call.cost_usd and call.cost_usd > 0
    assert call.finish_reason == "stop"


def test_openai_truncation_and_hallucinated_tool_are_detected() -> None:
    client = tervic.attach_llm(FakeOpenAI([chat_completion("The answer is", finish="length"),
                                           chat_completion("", finish="tool_calls", tool=("rm_rf", "{}"))]))
    with tervic.run("oa2") as run:
        client.chat.completions.create(model="gpt-4o-mini", messages=[])
        client.chat.completions.create(model="gpt-4o-mini", messages=[],
                                       tools=[{"type": "function", "function": {"name": "search"}}])
        run.set_output("x")
    assert {"TV201", "TV203"} <= ids(run.report) if run.wait() else False


def test_anthropic_message_recorded() -> None:
    message = anthropic_types.Message.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": "Hello"}, {"type": "tool_use", "id": "t", "name": "search",
                                                         "input": {"q": "x"}}],
        "stop_reason": "max_tokens", "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 3},
    })
    client = tervic.attach_llm(FakeAnthropic(message))
    with tervic.run("an") as run:
        assert client.messages.create(model="claude-haiku-4-5", system="be nice", messages=[]) is message
    call = _llm_calls(run)[0]
    assert call.provider == "anthropic" and call.output == "Hello" and call.finish_reason == "max_tokens"
    assert call.tool_calls[0].name == "search" and call.input_messages["system"] == "be nice"


def test_gemini_shape_recorded() -> None:
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"),
                                    content=SimpleNamespace(parts=[SimpleNamespace(text="Hi", function_call=None)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=4, candidates_token_count=1), model_version="gemini-2.5-flash",
    )
    parsed = parse_gemini(response)
    assert parsed["finish_reason"] == "length" and parsed["output"] == "Hi"
    client = tervic.attach_llm(FakeGemini(response))
    with tervic.run("gm") as run:
        client.models.generate_content(model="gemini-2.5-flash", contents="hi")
    assert _llm_calls(run)[0].provider == "gemini"


def test_unknown_client_is_reported_not_raised() -> None:
    from tervic.core.safety import internal_error_counts

    obj = object()
    assert tervic.attach_llm(obj) is obj
    assert "attach_llm" in internal_error_counts()


def test_calls_outside_a_run_are_not_recorded() -> None:
    client = tervic.attach_llm(FakeOpenAI([chat_completion("x")]))
    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    tervic.flush()
    assert store_for().list_runs() == []


def test_replay_serves_recorded_response_and_offline_miss_raises() -> None:
    response = chat_completion("recorded answer")
    fake = FakeOpenAI([response])
    client = tervic.attach_llm(fake)
    with tervic.run("rec") as run:
        client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "q"}])
    tervic.flush()
    cassette = Cassette.load(store_for().run_dir(run.run_id) / Cassette.FILENAME)
    assert cassette.count("llm") == 1
    set_replay_state(ReplayState(cassette, from_step=None, offline=True))
    try:
        with tervic.run("replayed"):
            served = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "q"}])
            with pytest.raises(ReplayMiss):
                client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "new"}])
    finally:
        set_replay_state(None)
    assert served.choices[0].message.content == "recorded answer"
    assert fake.calls == 1 and cassette.hits == 1


async def test_async_client_wrapping() -> None:
    response = chat_completion("async")

    class AsyncCompletions:
        async def create(self, **kwargs: Any) -> Any:
            return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=AsyncCompletions()))
    tervic.attach_llm(client)
    async with tervic.run("async-llm") as run:
        assert await client.chat.completions.create(model="gpt-4o-mini", messages=[]) is response
    assert _llm_calls(run)[0].output == "async"
