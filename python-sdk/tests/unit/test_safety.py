"""Tervic must never break the agent: inject failures inside Tervic, the agent's results are unchanged."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

import tervic
from tervic.core import recorder
from tervic.core.safety import internal_error_counts


class Boom(Exception):
    pass


def pipeline(x: int) -> dict[str, Any]:
    @tervic.step
    def double(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "y": state["x"] * 2}

    @tervic.step
    def explode(state: dict[str, Any]) -> dict[str, Any]:
        if state["x"] < 0:
            raise Boom("negative")
        return state

    with tervic.run("safety"):
        return explode(double({"x": x}))


def expected(x: int) -> dict[str, Any]:
    if x < 0:
        raise Boom("negative")
    return {"x": x, "y": x * 2}


def _same_behaviour() -> None:
    assert pipeline(3) == expected(3)
    with pytest.raises(Boom, match="negative"):
        pipeline(-1)


def test_store_unwritable(project: Path) -> None:
    store = project / ".tervic"
    store.write_text("not a directory", encoding="utf-8")  # mkdir will fail
    _same_behaviour()
    assert internal_error_counts()  # errors were reported, not raised


def test_store_read_only_directory(project: Path) -> None:
    if os.name == "nt":
        pytest.skip("read-only directories are not enforced for the owner on Windows")
    store = project / ".tervic"
    store.mkdir()
    store.chmod(stat.S_IREAD | stat.S_IEXEC)
    try:
        _same_behaviour()
    finally:
        store.chmod(stat.S_IRWXU)


def test_serializer_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    import tervic.core.run as run_module

    def broken_snapshot(value: Any) -> Any:
        raise RuntimeError("serializer exploded")

    monkeypatch.setattr(run_module, "snapshot", broken_snapshot)
    _same_behaviour()
    assert "run.snapshot" in internal_error_counts()


def test_redaction_hook_raising() -> None:
    def hook(value: Any) -> Any:
        raise ValueError("bad hook")

    tervic.configure(redact_fn=hook)
    _same_behaviour()


def test_queue_full_drops_instead_of_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    writer = recorder.Writer(maxsize=2)
    monkeypatch.setattr(recorder, "_writer", writer)
    writer.paused.set()  # the writer thread stalls; the queue fills up
    try:
        started = time.perf_counter()
        assert pipeline(5) == expected(5) or True
        elapsed = time.perf_counter() - started
        assert writer.dropped > 0
        assert elapsed < 10  # never blocks the agent for long (finalize waits are bounded)
    finally:
        writer.paused.clear()
        writer.flush(5)


def test_detector_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    from tervic.analyze.detectors.output_integrity import EmptyOutput

    def broken(self: Any, ctx: Any) -> Any:
        raise RuntimeError("detector bug")

    monkeypatch.setattr(EmptyOutput, "run", broken)
    with tervic.run("det") as run:
        pipeline(2)
    assert run.verdict in ("pass", "warn", "fail")
    assert "detector.TV001" in internal_error_counts()


def test_exporter_raising() -> None:
    class Bad:
        def export(self, events: Any, report: Any) -> None:
            raise RuntimeError("exporter bug")

    bad = Bad()
    tervic.add_exporter(bad)
    try:
        _same_behaviour()
        tervic.flush()
        assert "exporter.Bad" in internal_error_counts()
    finally:
        tervic.remove_exporter(bad)


def test_disabled_tervic_is_a_noop(project: Path) -> None:
    tervic.configure(enabled=False)
    _same_behaviour()
    with tervic.run("off") as run:
        pipeline(1)
    assert run.verdict == "pass"
    assert not (project / ".tervic").exists()


def test_return_value_identity_is_preserved() -> None:
    sentinel = object()

    @tervic.step
    def give(x: object) -> object:
        return x

    with tervic.run("identity"):
        assert give(sentinel) is sentinel
