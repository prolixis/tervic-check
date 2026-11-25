"""50 concurrent asyncio runs and 8 threads: no event mix-up between runs."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import tervic
from tervic.analyze.model import build_view
from tervic.api import store_for


@tervic.step
async def async_fetch(state: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.001)
    return {**state, "fetched": state["n"]}


@tervic.step
async def async_answer(state: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.001)
    return {**state, "answer": f"n={state['fetched']}"}


@tervic.step
def sync_fetch(state: dict[str, Any]) -> dict[str, Any]:
    return {**state, "fetched": state["n"]}


@tervic.step
def sync_answer(state: dict[str, Any]) -> dict[str, Any]:
    return {**state, "answer": f"n={state['fetched']}"}


def _assert_isolated(runs: list[Any]) -> None:
    tervic.flush()
    store = store_for()
    for run in runs:
        n = run.tags["n"]
        view = build_view(store.load_events(run.run_id))
        assert [s.name for s in view.steps][-2:] == [view.steps[-2].name, view.steps[-1].name]
        for step in view.steps:
            assert step.input["n"] == int(n), (run.run_id, step.name, step.input)
        assert {e.run_id for e in store.load_events(run.run_id)} == {run.run_id}


async def test_fifty_concurrent_asyncio_runs() -> None:
    async def one(n: int) -> Any:
        async with tervic.run("async-run", tags={"n": str(n)}) as run:
            state = await async_fetch({"n": n})
            await async_answer(state)
        return run

    runs = await asyncio.gather(*(one(n) for n in range(50)))
    assert len({r.run_id for r in runs}) == 50
    for run in runs:
        assert len(run.events) == 6  # run start, 2 x (step start, step end), run end
    _assert_isolated(runs)


def test_eight_threads() -> None:
    runs: list[Any] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        for _ in range(5):
            with tervic.run("thread-run", tags={"n": str(n)}) as run:
                sync_answer(sync_fetch({"n": n}))
            with lock:
                runs.append(run)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(runs) == 40
    _assert_isolated(runs)
