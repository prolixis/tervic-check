from __future__ import annotations

import dataclasses
import json
from typing import Any

from pydantic import BaseModel

from tervic.core.serialize import (
    MARK,
    MAX_ITEMS,
    MAX_KEYS,
    MAX_STR,
    is_empty,
    is_marker,
    item_count,
    snapshot,
    state_diff,
    text_of,
    value_type,
)


class Doc(BaseModel):
    title: str
    tags: list[str]


@dataclasses.dataclass
class Point:
    x: int
    y: int


class FakeArray:
    shape = (3, 4)
    dtype = "float32"

    def tolist(self) -> list[Any]:
        return []


class HumanMessage:
    type = "human"

    def __init__(self, content: str) -> None:
        self.content = content


class Weird:
    def __repr__(self) -> str:
        raise RuntimeError("no repr for you")


def test_plain_values_round_trip() -> None:
    value = {"a": 1, "b": [1.5, None, True], "c": "text"}
    assert snapshot(value) == value
    json.dumps(snapshot(value))


def test_pydantic_dataclass_and_message() -> None:
    assert snapshot(Doc(title="t", tags=["x"])) == {"title": "t", "tags": ["x"]}
    assert snapshot(Point(1, 2)) == {"x": 1, "y": 2}
    msg = snapshot(HumanMessage("hello"))
    assert msg[MARK] == "message" and msg["role"] == "human" and msg["content"] == "hello"


def test_bytes_and_arrays_keep_metadata_only() -> None:
    raw = snapshot(b"secret bytes")
    assert raw[MARK] == "bytes" and raw["len"] == 12 and len(raw["sha256"]) == 64
    arr = snapshot(FakeArray())
    assert arr == {MARK: "ndarray", "type": f"{__name__}.FakeArray", "shape": [3, 4], "dtype": "float32"}


def test_long_strings_keep_length_and_hash() -> None:
    text = "x" * (MAX_STR + 100)
    snap = snapshot(text)
    assert snap[MARK] == "str" and snap["len"] == MAX_STR + 100 and len(snap["head"]) == MAX_STR
    assert not is_empty(snap) and value_type(snap) == "str"


def test_limits_on_keys_items_and_depth() -> None:
    big = {f"k{i}": i for i in range(MAX_KEYS + 5)}
    assert snapshot(big)[f"{MARK}_truncated_keys"] == 5
    items = snapshot(list(range(MAX_ITEMS + 7)))
    assert items[-1] == {MARK: "more", "count": 7} and item_count(items) == MAX_ITEMS + 7
    deep: dict[str, Any] = {}
    cursor = deep
    for _ in range(20):
        cursor["n"] = {}
        cursor = cursor["n"]
    flat = json.dumps(snapshot(deep))
    assert '"reason": "depth"' in flat


def test_cycles_and_unrepresentable_objects() -> None:
    a: dict[str, Any] = {"name": "a"}
    a["self"] = a
    assert snapshot(a)["self"][MARK] == "cycle"
    weird = snapshot(Weird())
    assert weird[MARK] == "object" and "unrepresentable" in weird["repr"]


def test_non_finite_floats_and_sets() -> None:
    assert snapshot(float("nan")) == "nan"
    assert snapshot({3, 1, 2}) == [1, 2, 3]


def test_helpers() -> None:
    for empty in (None, "", "   ", [], {}, {MARK: "str", "len": 0}):
        assert is_empty(empty)
    for full in ("x", [0], {"a": None}, 0, False, {MARK: "object"}):
        assert not is_empty(full)
    assert value_type([1]) == "list" and value_type({"a": 1}) == "dict" and value_type(None) == "null"
    assert is_marker({MARK: "bytes"}, "bytes")
    assert "needle" in text_of({"a": ["x", {"b": "needle"}]})
    assert item_count("x") is None


def test_state_diff() -> None:
    diff = state_diff({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    assert diff == {"added": ["c"], "removed": [], "changed": ["b"]}
    assert state_diff({"a": 1}, {}) == {"added": [], "removed": ["a"], "changed": []}
    assert state_diff("x", {}) is None
