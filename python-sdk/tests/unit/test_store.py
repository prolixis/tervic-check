from __future__ import annotations

from pathlib import Path

import pytest
from helpers import node, record

import tervic
from tervic.api import store_for
from tervic.store.cassette import Cassette, NotReplayable, decode, encode
from tervic.store.local import GITIGNORE, LocalStore, RunNotFound


def test_layout_and_gitignore(project: Path) -> None:
    report = record(lambda run: node(run, "a", {}, {"x": 1}) and "ok")
    root = project / ".tervic"
    assert (root / ".gitignore").read_text(encoding="utf-8") == GITIGNORE
    assert (root / "index.sqlite").is_file()
    run_dir = root / "runs" / report.run_id
    assert (run_dir / "events.jsonl").is_file() and (run_dir / "report.json").is_file()


def test_resolve_last_prefix_and_errors() -> None:
    store = store_for()
    with pytest.raises(RunNotFound, match="No runs"):
        store.resolve("last")
    first = record(lambda run: "a")
    second = record(lambda run: "b")
    assert store.resolve(None) == second.run_id
    assert store.resolve(second.run_id[:20]) == second.run_id
    assert store.resolve(first.run_id) == first.run_id
    with pytest.raises(RunNotFound, match="not found"):
        store.resolve("ZZZZ")


def test_retention_gc() -> None:
    tervic.configure(retention_runs=3)
    reports = [record(lambda run: "x") for _ in range(5)]
    store = store_for()
    kept = [row.run_id for row in store.list_runs(limit=10)]
    assert kept == [r.run_id for r in reversed(reports[-3:])]
    assert not store.run_dir(reports[0].run_id).exists()
    assert store.gc(1) == [r.run_id for r in reversed(reports[-3:-1])]


def test_partial_trailing_line_is_ignored(project: Path) -> None:
    report = record(lambda run: "x")
    path = store_for().run_dir(report.run_id) / "events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "step_started", "run_id": "broken')
    assert len(store_for().load_events(report.run_id)) == 2


def test_list_filters() -> None:
    record(lambda run: "ok", name="a")
    record(lambda run: node(run, "r", {}, [], kind="retriever", mode=None) and None, name="b")
    store = store_for()
    assert [r.name for r in store.list_runs(verdict="fail")] == ["b"]
    assert [r.name for r in store.list_runs(name="a")] == ["a"]
    assert store.get_row("nope") is None


def test_cassette_exact_encoding_round_trip() -> None:
    from pydantic import BaseModel

    class Local(BaseModel):
        a: int

    value = {"t": (1, 2), "n": [None, 1.5, "s"], "nested": {"k": True}}
    assert decode(encode(value)) == value
    with pytest.raises(NotReplayable, match="inside a function"):
        encode(Local(a=1))
    with pytest.raises(NotReplayable):
        encode({1: "non-string key"})
    with pytest.raises(NotReplayable):
        encode(object())


def test_cassette_take_order_and_unreplayable(tmp_path: Path) -> None:
    cassette = Cassette(tmp_path / "c.jsonl")
    cassette.record("step", "k", {"v": 1})
    cassette.record("step", "k", {"v": 2})
    assert not cassette.record("step", "x", object())
    loaded = Cassette.load(tmp_path / "c.jsonl")
    assert loaded.take("step", "k") == (True, {"v": 1})
    assert loaded.take("step", "k") == (True, {"v": 2})
    assert loaded.take("step", "k") == (False, None)
    assert loaded.take("step", "x") == (False, None) and loaded.live
    assert loaded.hits == 2 and loaded.misses == 2


def test_secrets_never_reach_the_cassette() -> None:
    @tervic.step
    def leaky(x: str) -> str:
        return f"token sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234 for {x}"

    with tervic.run("leak") as run:
        leaky("u")
    tervic.flush()
    text = (store_for().run_dir(run.run_id) / Cassette.FILENAME).read_text(encoding="utf-8")
    assert "sk-proj" not in text and '"replayable": false' in text


def test_store_on_custom_absolute_dir(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    tervic.configure(store_dir=str(target))
    record(lambda run: "x")
    assert LocalStore(target).list_runs()
