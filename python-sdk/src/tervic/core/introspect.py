"""Static facts about a step function: where it is defined and which state fields it reads.

Field reads are found by parsing the function's source once (cached) and collecting
``state["x"]``, ``state.get("x")`` and ``state.x`` on the function's first parameter. This gives the
detectors a deterministic, offline idea of which fields each step consumes.
"""

from __future__ import annotations

import ast
import functools
import inspect
import linecache
import os
import textwrap
from collections.abc import Callable
from typing import Any

_IGNORED_ATTRS = frozenset(
    {
        "get",
        "items",
        "keys",
        "values",
        "copy",
        "update",
        "pop",
        "setdefault",
        "model_dump",
        "model_copy",
        "dict",
        "json",
        "__class__",
        "__dict__",
    }
)


def _unwrap(fn: Any) -> Any:
    seen = 0
    while hasattr(fn, "__wrapped__") and seen < 20:
        fn = fn.__wrapped__
        seen += 1
    return getattr(fn, "__func__", fn)


@functools.lru_cache(maxsize=4096)
def _source_location(code_id: int, filename: str, line: int) -> str:
    # co_firstlineno points at the first decorator; move to the `def` line.
    for offset in range(50):
        text = linecache.getline(filename, line + offset).lstrip()
        if not text:
            break
        if text.startswith(("def ", "async def ")):
            line += offset
            break
    try:
        rel = os.path.relpath(filename)
    except ValueError:
        rel = filename
    if rel.startswith(".."):
        rel = filename
    return f"{rel.replace(os.sep, '/')}:{line}"


def source_of(fn: Callable[..., Any]) -> str | None:
    """``path:line`` of the function definition, relative to the working directory."""
    try:
        target = _unwrap(fn)
        code = getattr(target, "__code__", None)
        if code is None:
            return None
        return _source_location(id(code), code.co_filename, code.co_firstlineno)
    except Exception:
        return None


_reads_cache: dict[Any, tuple[str, ...]] = {}


def reads_of(fn: Callable[..., Any]) -> list[str]:
    """State fields the function reads from its first parameter (best effort, never raises)."""
    try:
        target = _unwrap(fn)
        code = getattr(target, "__code__", None)
        if code is None:
            return []
        if code in _reads_cache:
            return list(_reads_cache[code])
        result = tuple(sorted(_parse_reads(target)))
        _reads_cache[code] = result
        return list(result)
    except Exception:
        return []


def _parse_reads(fn: Any) -> set[str]:
    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return set()
    tree = ast.parse(source)
    func = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)), None)
    if func is None:
        return set()
    params = [a.arg for a in func.args.posonlyargs + func.args.args]
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    if not params:
        return set()
    state = params[0]
    found: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Subscript) and _is_name(node.value, state):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found.add(key.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and _is_name(node.func.value, state) and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
        elif (
            isinstance(node, ast.Attribute)
            and _is_name(node.value, state)
            and node.attr not in _IGNORED_ATTRS
            and not node.attr.startswith("_")
            and isinstance(node.ctx, ast.Load)
        ):
            found.add(node.attr)
    return found


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name
