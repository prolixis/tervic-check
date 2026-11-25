from __future__ import annotations

from typing import Any

import pytest

import tervic
from tervic.analyze.model import build_view
from tervic.api import store_for


def _view(run: Any) -> Any:
    tervic.flush()
    return build_view(store_for().load_events(run.run_id))


def test_sync_async_and_generators() -> None:
    @tervic.step
    def sync_step(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "a": 1}

    @tervic.step(name="custom-name", kind="tool")
    async def async_step(x: int, y: int = 2) -> int:
        return x + y

    @tervic.step
    def gen_step(n: int) -> Any:
        total = 0
        for i in range(n):
            sent = yield i
            total += sent or 0
        return total

    @tervic.step
    async def agen_step(n: int) -> Any:
        for i in range(n):
            yield i * 10

    import asyncio

    with tervic.run("mixed") as run:
        assert sync_step({"q": 1}) == {"q": 1, "a": 1}
        assert asyncio.run(async_step(1)) == 3
        gen = gen_step(3)
        assert next(gen) == 0 and gen.send(5) == 1 and gen.send(6) == 2
        with pytest.raises(StopIteration) as stop:
            gen.send(7)
        assert stop.value.value == 18

        async def collect() -> list[int]:
            return [i async for i in agen_step(3)]

        assert asyncio.run(collect()) == [0, 10, 20]
    view = _view(run)
    names = [s.name for s in view.steps]
    assert names == ["sync_step", "custom-name", "gen_step", "agen_step"]
    assert view.steps[1].kind == "tool" and view.steps[1].input == 1  # single argument recorded as-is
    assert view.steps[2].output == [0, 1, 2] and view.steps[3].output == [0, 10, 20]
    assert view.steps[0].state_mode == "replace" and view.steps[0].state_diff == {
        "added": ["a"], "removed": [], "changed": []}


def test_exceptions_pass_through_and_are_recorded() -> None:
    @tervic.step
    def fails(state: dict[str, Any]) -> None:
        raise KeyError("missing")

    with pytest.raises(KeyError), tervic.run("err") as run:
        fails({"q": 1})
    assert run.status == "crashed"
    view = _view(run)
    assert view.steps[0].error is not None and view.steps[0].error.type == "KeyError"
    assert run.verdict == "fail"


def test_generator_closed_early() -> None:
    @tervic.step
    def numbers() -> Any:
        yield from range(100)

    with tervic.run("early") as run:
        for value in numbers():
            if value == 2:
                break
    view = _view(run)
    assert view.steps[0].output == [0, 1, 2] and view.steps[0].error is None


def test_step_outside_run_becomes_its_own_run() -> None:
    @tervic.step
    def standalone(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "done": True}

    standalone({"x": 1})
    tervic.flush()
    rows = store_for().list_runs()
    assert rows and rows[0].name == "standalone"


def test_nested_steps_have_parents_and_nested_runs_join() -> None:
    @tervic.step
    def inner(x: int) -> int:
        return x + 1

    @tervic.step
    def outer(x: int) -> int:
        with tervic.run("ignored-inner-run"):
            return inner(x) * 2

    with tervic.run("nest") as run:
        assert outer(1) == 4
    view = _view(run)
    assert [s.name for s in view.steps] == ["outer", "inner"]
    assert view.steps[1].parent_step_id == view.steps[0].step_id
    assert len(store_for().list_runs()) == 1


def test_capture_input_false_and_invalid_kind() -> None:
    @tervic.step(capture_input=False, capture_output=False)
    def private(secret: str) -> str:
        return secret.upper()

    with tervic.run("priv") as run:
        assert private("hush") == "HUSH"
    view = _view(run)
    assert view.steps[0].input is None and view.steps[0].output is None
    with pytest.raises(ValueError, match="kind must be one of"):
        tervic.step(kind="banana")


def test_static_reads_and_redaction_in_stored_events() -> None:
    @tervic.step
    def uses(state: dict[str, Any]) -> dict[str, Any]:
        return {"answer": state["question"] + str(state.get("context")), "k": state.key if False else 1}

    with tervic.run("reads") as run:
        uses({"question": "q", "context": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234"})
    view = _view(run)
    assert set(view.steps[0].reads) >= {"question", "context"}
    raw = (store_for().run_dir(run.run_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "sk-proj-AbCdEfGh" not in raw and "[REDACTED:api_key:" in raw


def test_retry_attempts_are_counted() -> None:
    calls = {"n": 0}

    @tervic.step
    def flaky(x: int) -> int:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("again")
        return x

    with tervic.run("retry") as run:
        for _ in range(3):
            try:
                flaky(1)
            except TimeoutError:
                continue
    view = _view(run)
    assert [s.attempt for s in view.steps] == [1, 2, 3]


def test_run_level_api() -> None:
    with tervic.run("api", tags={"env": "test"}, input={"q": 1}) as run:
        assert tervic.current_run() is run
        run.set_output("final")
    assert tervic.current_run() is None
    assert run.verdict == "pass" and run.findings == []
    report = tervic.check()
    assert report.run_id == run.run_id and report.tags == {"env": "test"}
