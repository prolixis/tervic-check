"""Turn any Python object into a bounded, JSON-safe snapshot.

Limits: depth 8, 200 keys per dict, 200 items per list, strings cut at 4,000 characters. A cut
string keeps its full length and the sha256 of the full value, so emptiness and changes can still
be detected. Special values are written as dicts with a ``"__tervic__"`` marker key.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Mapping
from typing import Any

MAX_DEPTH = 8
MAX_KEYS = 200
MAX_ITEMS = 200
MAX_STR = 4000
MAX_REPR = 500
MARK = "__tervic__"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _string(value: str) -> Any:
    if len(value) <= MAX_STR:
        return value
    return {
        MARK: "str",
        "len": len(value),
        "sha256": _sha256(value.encode("utf-8", "surrogatepass")),
        "head": value[:MAX_STR],
    }


def _safe_repr(obj: Any) -> str:
    try:
        text = repr(obj)
    except Exception as exc:
        text = f"<unrepresentable {type(obj).__name__}: {type(exc).__name__}>"
    return text if len(text) <= MAX_REPR else text[:MAX_REPR] + "..."


def _qualname(obj: Any) -> str:
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


def _message(obj: Any) -> dict[str, Any] | None:
    """LangChain-style message: has ``type`` (role) and ``content`` attributes."""
    msg_type = getattr(obj, "type", None)
    if not isinstance(msg_type, str) or not hasattr(obj, "content"):
        return None
    if not any(cls.__name__.endswith("Message") for cls in type(obj).__mro__):
        return None
    out: dict[str, Any] = {MARK: "message", "role": msg_type, "content": obj.content}
    tool_calls = getattr(obj, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = tool_calls
    name = getattr(obj, "name", None)
    if name:
        out["name"] = name
    return out


def _array(obj: Any) -> dict[str, Any] | None:
    shape = getattr(obj, "shape", None)
    dtype = getattr(obj, "dtype", None)
    if shape is None or dtype is None or not hasattr(obj, "tolist"):
        return None
    try:
        return {MARK: "ndarray", "type": _qualname(obj), "shape": list(shape), "dtype": str(dtype)}
    except Exception:
        return None


def snapshot(obj: Any) -> Any:
    """Return a JSON-safe snapshot of ``obj``. Never raises."""
    try:
        return _snap(obj, 0, set())
    except Exception as exc:
        return {MARK: "error", "type": _qualname(obj), "error": f"{type(exc).__name__}: {exc}"}


def _snap(obj: Any, depth: int, seen: set[int]) -> Any:
    if obj is None or isinstance(obj, bool | int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else str(obj)
    if isinstance(obj, str):
        return _string(obj)
    if isinstance(obj, bytes | bytearray | memoryview):
        raw = bytes(obj)
        return {MARK: "bytes", "len": len(raw), "sha256": _sha256(raw)}
    if depth >= MAX_DEPTH:
        return {MARK: "truncated", "reason": "depth", "type": _qualname(obj)}

    oid = id(obj)
    if oid in seen:
        return {MARK: "cycle", "type": _qualname(obj)}
    seen.add(oid)
    try:
        return _snap_container(obj, depth, seen)
    finally:
        seen.discard(oid)


def _snap_container(obj: Any, depth: int, seen: set[int]) -> Any:
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for index, (key, value) in enumerate(obj.items()):
            if index >= MAX_KEYS:
                out[f"{MARK}_truncated_keys"] = len(obj) - MAX_KEYS
                break
            out[key if isinstance(key, str) else _safe_repr(key)] = _snap(value, depth + 1, seen)
        return out
    if isinstance(obj, list | tuple | set | frozenset):
        items = list(obj) if not isinstance(obj, set | frozenset) else sorted(obj, key=_safe_repr)
        result = [_snap(item, depth + 1, seen) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            result.append({MARK: "more", "count": len(items) - MAX_ITEMS})
        return result

    message = _message(obj)
    if message is not None:
        return _snap(message, depth, seen)
    array = _array(obj)
    if array is not None:
        return array

    dump = getattr(obj, "model_dump", None)
    if callable(dump) and hasattr(type(obj), "model_fields"):
        return _snap(dump(mode="python"), depth, seen)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        fields = {f.name: getattr(obj, f.name, None) for f in dataclasses.fields(obj)}
        return _snap(fields, depth, seen)
    to_dict = getattr(obj, "to_dict", None) or getattr(obj, "dict", None)
    if callable(to_dict) and not isinstance(obj, type):
        try:
            data = to_dict()
        except Exception:
            data = None
        if isinstance(data, Mapping):
            return _snap(data, depth, seen)
    return {MARK: "object", "type": _qualname(obj), "repr": _safe_repr(obj)}


# ---------------------------------------------------------------- helpers used by detectors


def is_marker(value: Any, kind: str | None = None) -> bool:
    return isinstance(value, dict) and MARK in value and (kind is None or value[MARK] == kind)


def is_empty(value: Any) -> bool:
    """True for None, empty or whitespace-only strings, and empty containers."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if is_marker(value, "str"):
        return int(value.get("len", 0)) == 0
    if is_marker(value, "bytes"):
        return int(value.get("len", 0)) == 0
    if is_marker(value):
        return False
    if isinstance(value, dict | list):
        return len(value) == 0
    return False


def value_type(value: Any) -> str:
    """A stable, JSON-level type name (understands Tervic markers)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str) or is_marker(value, "str"):
        return "str"
    if is_marker(value):
        return str(value[MARK])
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def text_of(value: Any) -> str:
    """Flatten a snapshot to searchable text (bounded)."""
    parts: list[str] = []
    budget = [200_000]

    def walk(v: Any) -> None:
        if budget[0] <= 0:
            return
        if isinstance(v, str):
            parts.append(v)
            budget[0] -= len(v)
        elif is_marker(v, "str"):
            head = str(v.get("head", ""))
            parts.append(head)
            budget[0] -= len(head)
        elif isinstance(v, dict):
            for k, item in v.items():
                if k == MARK:
                    continue
                parts.append(str(k))
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)
        elif v is not None:
            parts.append(str(v))

    walk(value)
    return "\n".join(parts)


def item_count(value: Any) -> int | None:
    """Number of items in a list snapshot (counting truncated items)."""
    if not isinstance(value, list):
        return None
    total = 0
    for item in value:
        total += int(item.get("count", 0)) if is_marker(item, "more") else 1
    return total


def state_diff(before: Any, after: Any) -> dict[str, list[str]] | None:
    """Keys added / removed / changed between two dict snapshots."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    added = sorted(k for k in after if k not in before and k != MARK)
    removed = sorted(k for k in before if k not in after and k != MARK)
    changed = sorted(k for k in after if k in before and k != MARK and after[k] != before[k])
    return {"added": added, "removed": removed, "changed": changed}
