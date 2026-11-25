"""Helpers that guarantee a Tervic bug never changes what the user's agent does.

Every internal entry point (recorder, adapters, detectors, exporters, writers) runs inside
:func:`guard` or a function decorated with :func:`safe`. Internal exceptions are logged once per
location and swallowed. Nothing here ever wraps the user's own call.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger("tervic")

P = ParamSpec("P")
R = TypeVar("R")

_lock = threading.Lock()
_reported: set[str] = set()
_counts: dict[str, int] = {}


def report_internal_error(where: str, exc: BaseException) -> None:
    """Record an internal error. Logs the first occurrence per location, counts the rest."""
    try:
        with _lock:
            _counts[where] = _counts.get(where, 0) + 1
            first = where not in _reported
            _reported.add(where)
        if first:
            logger.warning(
                "Tervic internal error in %s (your agent is unaffected; further errors here are "
                "counted, not logged): %s: %s",
                where,
                type(exc).__name__,
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
    except Exception:  # pragma: no cover - logging itself failed
        pass


def internal_error_counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def reset_internal_errors() -> None:
    with _lock:
        _counts.clear()
        _reported.clear()


@contextmanager
def guard(where: str) -> Iterator[None]:
    """Swallow and report any exception raised by Tervic code inside the block."""
    try:
        yield
    except Exception as exc:
        report_internal_error(where, exc)


def safe(where: str, default: Any = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: run Tervic code, return ``default`` if it raises."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                report_internal_error(where, exc)
                return default  # type: ignore[no-any-return]

        return wrapper

    return decorator
