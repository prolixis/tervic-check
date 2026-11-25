"""Cassettes: exact recordings of step outputs and LLM/tool responses, used by replay.

Only values that round-trip safely are stored exactly: JSON-native values, pydantic models
(rebuilt from their import path) and dataclasses of those. Pickle is never used, so a cassette
file can never execute code when loaded. A value that cannot be stored exactly is marked
``replayable: false`` and that step runs live during replay.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import threading
from pathlib import Path
from typing import Any

_EXACT = "__tervic_exact__"


class NotReplayable(Exception):
    pass


def _import(path: str) -> Any:
    module_name, _, attr = path.rpartition(".")
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("|"):
        obj = getattr(obj, part)
    return obj


def encode(value: Any, depth: int = 0) -> Any:
    """Encode ``value`` exactly or raise :class:`NotReplayable`."""
    if depth > 64:
        raise NotReplayable("too deep")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [encode(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return {_EXACT: "tuple", "items": [encode(v, depth + 1) for v in value]}
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise NotReplayable("non-string dict keys")
        return {_EXACT: "dict", "items": {k: encode(v, depth + 1) for k, v in value.items()}}
    cls = type(value)
    path = f"{cls.__module__}.{cls.__qualname__.replace('.', '|')}"
    if "<locals>" in cls.__qualname__:
        raise NotReplayable(f"{cls.__qualname__} is defined inside a function")
    if hasattr(cls, "model_fields") and callable(getattr(value, "model_dump", None)):
        return {_EXACT: "pydantic", "cls": path, "data": encode(value.model_dump(mode="json"), depth + 1)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: encode(getattr(value, f.name), depth + 1) for f in dataclasses.fields(value)}
        return {_EXACT: "dataclass", "cls": path, "fields": fields}
    raise NotReplayable(f"type {cls.__module__}.{cls.__qualname__} cannot be stored exactly")


def decode(data: Any) -> Any:
    if isinstance(data, list):
        return [decode(v) for v in data]
    if not isinstance(data, dict):
        return data
    kind = data.get(_EXACT)
    if kind == "dict":
        return {k: decode(v) for k, v in data["items"].items()}
    if kind == "tuple":
        return tuple(decode(v) for v in data["items"])
    if kind == "pydantic":
        return _import(data["cls"]).model_validate(decode(data["data"]))
    if kind == "dataclass":
        return _import(data["cls"])(**{k: decode(v) for k, v in data["fields"].items()})
    return {k: decode(v) for k, v in data.items()}


def make_entry(kind: str, key: str, value: Any, name: str | None = None) -> dict[str, Any]:
    """A cassette line for ``value``; non-storable values are marked ``replayable: false``."""
    try:
        return {"kind": kind, "key": key, "name": name, "replayable": True, "value": encode(value)}
    except NotReplayable as exc:
        return {"kind": kind, "key": key, "name": name, "replayable": False, "reason": str(exc)}


def request_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=repr)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class Cassette:
    """Append-only recording for one run, plus hit counting during replay."""

    FILENAME = "cassette.jsonl"

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.entries: list[dict[str, Any]] = []
        self._cursor: dict[tuple[str, str], int] = {}
        self.hits = 0
        self.misses = 0
        self.live: list[str] = []

    @classmethod
    def load(cls, path: Path) -> Cassette:
        cassette = cls(path)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        cassette.entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return cassette

    def record(self, kind: str, key: str, value: Any, *, name: str | None = None) -> bool:
        """Store a value. Returns False if it is not exactly storable."""
        entry = make_entry(kind, key, value, name)
        line = json.dumps(entry)
        with self._lock:
            self.entries.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return bool(entry["replayable"])

    def take(self, kind: str, key: str) -> tuple[bool, Any]:
        """Next recorded value for ``(kind, key)`` in recording order: ``(found, value)``."""
        with self._lock:
            start = self._cursor.get((kind, key), 0)
            for index in range(start, len(self.entries)):
                entry = self.entries[index]
                if entry["kind"] == kind and entry["key"] == key:
                    self._cursor[(kind, key)] = index + 1
                    if not entry.get("replayable"):
                        self.misses += 1
                        self.live.append(f"{kind}:{entry.get('name') or key} ({entry.get('reason')})")
                        return False, None
                    self.hits += 1
                    return True, decode(entry["value"])
            self.misses += 1
            return False, None

    def count(self, kind: str) -> int:
        return sum(1 for e in self.entries if e["kind"] == kind)
