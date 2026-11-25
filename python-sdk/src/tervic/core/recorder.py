"""Background writer: one daemon thread owns every disk write and the post-run analysis.

The agent's thread only puts items on a bounded queue (10,000 items). When the queue is full,
``prod`` drops immediately and counts the drop; ``dev``/``ci`` wait at most 50 ms, then drop.
"""

from __future__ import annotations

import atexit
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tervic.core.safety import report_internal_error

if TYPE_CHECKING:
    from tervic.analyze.engine import Report
    from tervic.core.events import Event
    from tervic.core.run import Run
    from tervic.export import Exporter

QUEUE_SIZE = 10_000
DEV_PUT_TIMEOUT_S = 0.05


class Writer:
    def __init__(self, maxsize: int = QUEUE_SIZE) -> None:
        self.queue: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.paused = threading.Event()  # tests use this to simulate a stalled writer

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="tervic-writer", daemon=True)
            self._thread.start()

    def submit_data(self, item: tuple[Any, ...], mode: str) -> bool:
        """Queue a data item from the agent's thread. Never blocks longer than 50 ms."""
        self._ensure_thread()
        try:
            if mode == "prod":
                self.queue.put_nowait(item)
            else:
                self.queue.put(item, timeout=DEV_PUT_TIMEOUT_S)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def submit_control(self, item: tuple[Any, ...], timeout: float = 5.0) -> bool:
        """Queue a control item (finalize/barrier). Used only at run end, never per step."""
        self._ensure_thread()
        try:
            self.queue.put(item, timeout=timeout)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def flush(self, timeout: float = 10.0) -> bool:
        done = threading.Event()
        if not self.submit_control(("barrier", done), timeout=timeout):
            return False
        return done.wait(timeout)

    def _loop(self) -> None:
        while True:
            item = self.queue.get()
            batch = [item]
            try:
                while len(batch) < 500:
                    batch.append(self.queue.get_nowait())
            except queue.Empty:
                pass
            try:
                while self.paused.is_set():
                    time.sleep(0.01)
                self._handle(batch)
            finally:
                for _ in batch:
                    self.queue.task_done()

    def _handle(self, batch: list[tuple[Any, ...]]) -> None:
        appends: dict[tuple[str, str], list[str]] = {}
        order: list[tuple[str, str]] = []

        def write_pending() -> None:
            for key in order:
                path = Path(key[0]) / key[1]
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write("\n".join(appends[key]) + "\n")
                except Exception as exc:
                    report_internal_error("writer.append", exc)
            appends.clear()
            order.clear()

        def add(folder: str, filename: str, lines: list[str]) -> None:
            key = (folder, filename)
            if key not in appends:
                appends[key] = []
                order.append(key)
            appends[key].extend(lines)

        for item in batch:
            kind = item[0]
            if kind in ("lines", "event", "cassette"):
                try:
                    if kind == "lines":
                        add(item[1], item[2], item[3])
                    elif kind == "event":
                        from tervic.core.events import event_to_json

                        add(item[1], item[2], [event_to_json(item[3])])
                    else:
                        add(item[1], item[2], [_cassette_line(item[3], item[4])])
                except Exception as exc:
                    report_internal_error("writer.serialize", exc)
                continue
            write_pending()
            if kind == "finalize":
                run: Run = item[1]
                try:
                    run._finalize()
                except Exception as exc:
                    report_internal_error("writer.finalize", exc)
                    run._mark_done()
            elif kind == "barrier":
                item[1].set()
            elif kind == "call":
                try:
                    item[1]()
                except Exception as exc:
                    report_internal_error("writer.call", exc)
        write_pending()


def _cassette_line(entry: dict[str, Any], redact: bool) -> str:
    """Never store an exact value that contains a secret while redaction is on."""
    import json

    from tervic.core.redact import find_secrets

    if entry.get("replayable") and redact:
        encoded = json.dumps(entry["value"])
        if find_secrets(encoded):
            entry = {key: entry[key] for key in ("kind", "key", "name")}
            entry.update(replayable=False, reason="contains a secret and redaction is on")
            return json.dumps(entry)
        return json.dumps({**entry, "value": json.loads(encoded)})
    return json.dumps(entry)


_writer: Writer | None = None
_writer_lock = threading.Lock()


def get_writer() -> Writer:
    global _writer
    if _writer is None:
        with _writer_lock:
            if _writer is None:
                _writer = Writer()
    return _writer


# ---------------------------------------------------------------- exporters

_exporters: list[Exporter] = []
_exporters_lock = threading.Lock()


def add_exporter(exporter: Exporter) -> None:
    with _exporters_lock:
        _exporters.append(exporter)


def remove_exporter(exporter: Exporter) -> None:
    with _exporters_lock:
        if exporter in _exporters:
            _exporters.remove(exporter)


def registered_exporters() -> list[Exporter]:
    with _exporters_lock:
        return list(_exporters)


def run_exporters(exporters: list[Exporter], events: list[Event], report: Report) -> None:
    for exporter in exporters:
        try:
            exporter.export(events, report)
        except Exception as exc:
            report_internal_error(f"exporter.{type(exporter).__name__}", exc)


# ---------------------------------------------------------------- hooks

_flush_hooks: list[Callable[[float], None]] = []
_run_listeners: list[Callable[[Run], None]] = []
_hooks_lock = threading.Lock()


def add_flush_hook(hook: Callable[[float], None]) -> None:
    """Adapters whose events arrive asynchronously register a hook that waits for them."""
    with _hooks_lock:
        if hook not in _flush_hooks:
            _flush_hooks.append(hook)


def run_flush_hooks(timeout: float) -> None:
    with _hooks_lock:
        hooks = list(_flush_hooks)
    for hook in hooks:
        try:
            hook(timeout)
        except Exception as exc:
            report_internal_error("flush_hook", exc)


def add_run_listener(listener: Callable[[Run], None]) -> None:
    """Called (in the writer thread) after each run has been analyzed."""
    with _hooks_lock:
        _run_listeners.append(listener)


def remove_run_listener(listener: Callable[[Run], None]) -> None:
    with _hooks_lock:
        if listener in _run_listeners:
            _run_listeners.remove(listener)


def notify_run_listeners(run: Run) -> None:
    with _hooks_lock:
        listeners = list(_run_listeners)
    for listener in listeners:
        try:
            listener(run)
        except Exception as exc:
            report_internal_error("run_listener", exc)


# ---------------------------------------------------------------- interpreter exit

_active_runs: set[Run] = set()
_active_lock = threading.Lock()
_exit_hooks: list[Callable[[], None]] = []


def register_active(run: Run) -> None:
    with _active_lock:
        _active_runs.add(run)


def unregister_active(run: Run) -> None:
    with _active_lock:
        _active_runs.discard(run)


def _at_exit() -> None:
    try:
        with _active_lock:
            leftover = list(_active_runs)
        for run in leftover:
            run.end(status="crashed", error=("InterpreterExit", "Process exited before the run ended."))
        if _writer is not None:
            _writer.flush(timeout=5.0)
    except Exception as exc:  # pragma: no cover
        report_internal_error("atexit", exc)


atexit.register(_at_exit)
