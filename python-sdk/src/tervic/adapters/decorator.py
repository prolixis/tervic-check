"""Plain Python: ``@tervic.step`` and ``tervic.run(...)``.

The wrapper never changes what the decorated function does: Tervic code runs before and after the
call inside its own guards, the function is called exactly once with the original arguments, and
its return value or exception passes through unchanged. (The one deliberate exception is
``tervic replay``, which serves recorded outputs for steps before ``--from``.)
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import AsyncGenerator, Callable, Generator
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from tervic.core.events import STEP_KINDS
from tervic.core.introspect import reads_of, source_of
from tervic.core.run import (
    _MISSING,
    Run,
    RunScope,
    current_run,
    pop_run,
    pop_step,
    push_run,
    push_step,
    replay_state,
)
from tervic.core.safety import report_internal_error

F = TypeVar("F", bound=Callable[..., Any])
_MAX_ITEMS = 200


def run(
    name: str, tags: dict[str, str] | None = None, *, input: Any = _MISSING, framework: str = "python"
) -> RunScope:
    """Record everything inside the block as one run: ``with tervic.run("agent") as run: ...``.

    ``input`` (optional) is the run's input; it lets ``tervic replay`` call your entry point again.
    Nested ``tervic.run`` blocks join the outer run.
    """
    return RunScope(name, tags=tags, framework=framework, input=input)


@dataclass
class _Started:
    run: Run
    step_id: str
    implicit: bool
    run_token: Any
    served: bool = False
    served_value: Any = None
    single: bool = False


class _StepSpec:
    def __init__(
        self, fn: Callable[..., Any], name: str | None, kind: str, capture_input: bool, capture_output: bool
    ) -> None:
        self.fn = fn
        self.name: str = name or str(getattr(fn, "__name__", "step"))
        self.kind = kind
        self.capture_input = capture_input
        self.capture_output = capture_output
        self.source = source_of(fn)
        try:
            self.signature: inspect.Signature | None = inspect.signature(fn)
        except (TypeError, ValueError):
            self.signature = None
        self._reads: list[str] | None = None

    def reads(self) -> list[str]:
        if self._reads is None:
            self._reads = reads_of(self.fn)
        return self._reads

    def input_of(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, bool]:
        """(value to snapshot, is single state argument)."""
        if self.signature is None:
            return {"args": list(args), "kwargs": kwargs}, False
        try:
            bound = self.signature.bind_partial(*args, **kwargs).arguments
        except TypeError:
            return {"args": list(args), "kwargs": kwargs}, False
        values = {k: v for k, v in bound.items() if k not in ("self", "cls")}
        if len(values) == 1:
            return next(iter(values.values())), True
        return values, False

    # ------------------------------------------------------------------ begin / finish
    def begin(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> _Started | None:
        try:
            active = current_run()
            implicit = False
            run_token = None
            if active is None:
                active = Run(self.name, framework="python")
                if not active.active:
                    return None
                implicit = True
                run_token = push_run(active)
            if not active.active:
                return None
            value, single = self.input_of(args, kwargs)
            step_id = active.step_start(
                self.name,
                self.kind,
                value,
                source=self.source,
                capture_input=self.capture_input,
                reads=self.reads() if single else None,
            )
            started = _Started(active, step_id, implicit, run_token, single=single)
            replay = replay_state()
            if replay is not None and replay.before_from(self.name):
                found, recorded = replay.cassette.take("step", f"{self.name}#{active.occurrence(self.name)}")
                if found:
                    started.served = True
                    started.served_value = recorded
                else:
                    replay.cassette.live.append(f"step:{self.name}")
            return started
        except Exception as exc:
            report_internal_error("step.begin", exc)
            return None

    def finish(self, started: _Started | None, result: Any, *, exact: bool = True) -> None:
        if started is None:
            return
        try:
            started.run.step_end(
                started.step_id,
                result,
                state_mode="auto" if started.single else None,
                capture_output=self.capture_output,
                exact_output=result if exact else _MISSING,
            )
        except Exception as exc:
            report_internal_error("step.finish", exc)
        self._close_implicit(started, None, result)

    def fail(self, started: _Started | None, exc: BaseException) -> None:
        if started is None:
            return
        try:
            started.run.step_end(started.step_id, None, error=exc)
        except Exception as inner:
            report_internal_error("step.fail", inner)
        self._close_implicit(started, exc, None)

    def _close_implicit(self, started: _Started, exc: BaseException | None, result: Any) -> None:
        if not started.implicit:
            return
        try:
            if started.run_token is not None:
                pop_run(started.run_token)
            if exc is not None:
                started.run.end(status="crashed", error=exc)
            else:
                started.run.end(status="completed", final_output=result)
        except Exception as inner:
            report_internal_error("step.close_run", inner)


def _push(started: _Started | None) -> Any:
    if started is None:
        return None
    try:
        return push_step(started.step_id)
    except Exception as exc:
        report_internal_error("step.push", exc)
        return None


def _pop(token: Any) -> None:
    if token is not None:
        try:
            pop_step(token)
        except Exception as exc:
            report_internal_error("step.pop", exc)


def _wrap(spec: _StepSpec) -> Callable[..., Any]:
    fn = spec.fn
    if inspect.isasyncgenfunction(fn):

        @functools.wraps(fn)
        async def agen_wrapper(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, Any]:
            started = spec.begin(args, kwargs)
            items: list[Any] = []
            try:
                agen = fn(*args, **kwargs)
                try:
                    item = await agen.__anext__()
                except StopAsyncIteration:
                    spec.finish(started, items, exact=False)
                    return
                while True:
                    if len(items) < _MAX_ITEMS:
                        items.append(item)
                    try:
                        sent = yield item
                    except GeneratorExit:
                        await agen.aclose()
                        spec.finish(started, items, exact=False)
                        raise
                    except BaseException as thrown:
                        try:
                            item = await agen.athrow(thrown)
                        except StopAsyncIteration:
                            break
                    else:
                        try:
                            item = await agen.asend(sent)
                        except StopAsyncIteration:
                            break
            except GeneratorExit:
                raise
            except BaseException as exc:
                spec.fail(started, exc)
                raise
            spec.finish(started, items, exact=False)

        return agen_wrapper

    if inspect.isgeneratorfunction(fn):

        @functools.wraps(fn)
        def gen_wrapper(*args: Any, **kwargs: Any) -> Generator[Any, Any, Any]:
            started = spec.begin(args, kwargs)
            items: list[Any] = []
            try:
                gen = fn(*args, **kwargs)
                try:
                    item = next(gen)
                except StopIteration as stop:
                    spec.finish(started, items, exact=False)
                    return stop.value
                while True:
                    if len(items) < _MAX_ITEMS:
                        items.append(item)
                    try:
                        sent = yield item
                    except GeneratorExit:
                        gen.close()
                        spec.finish(started, items, exact=False)
                        raise
                    except BaseException as thrown:
                        try:
                            item = gen.throw(thrown)
                        except StopIteration as stop:
                            spec.finish(started, items, exact=False)
                            return stop.value
                    else:
                        try:
                            item = gen.send(sent)
                        except StopIteration as stop:
                            spec.finish(started, items, exact=False)
                            return stop.value
            except GeneratorExit:
                raise
            except BaseException as exc:
                spec.fail(started, exc)
                raise

        return gen_wrapper

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started = spec.begin(args, kwargs)
            if started is not None and started.served:
                spec.finish(started, started.served_value, exact=False)
                return started.served_value
            token = _push(started)
            try:
                result = await fn(*args, **kwargs)
            except BaseException as exc:
                _pop(token)
                spec.fail(started, exc)
                raise
            _pop(token)
            spec.finish(started, result)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started = spec.begin(args, kwargs)
        if started is not None and started.served:
            spec.finish(started, started.served_value, exact=False)
            return started.served_value
        token = _push(started)
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            _pop(token)
            spec.fail(started, exc)
            raise
        _pop(token)
        spec.finish(started, result)
        return result

    return sync_wrapper


@overload
def step(fn: F) -> F: ...


@overload
def step(
    fn: None = None,
    *,
    name: str | None = None,
    kind: str = "custom",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[[F], F]: ...


def step(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    kind: str = "custom",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Any:
    """Record a function as one step of the current run.

    ``@tervic.step`` or ``@tervic.step(name="retrieve", kind="retriever")``. Works on sync and async
    functions and generators. Called outside ``tervic.run(...)``, the call becomes its own run.
    ``capture_input=False`` keeps the arguments out of the record (privacy).
    """
    if kind not in STEP_KINDS:
        raise ValueError(f"kind must be one of {', '.join(STEP_KINDS)}; got {kind!r}")

    def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
        spec = _StepSpec(target, name, kind, capture_input, capture_output)
        wrapper = _wrap(spec)
        wrapper.__tervic_step__ = spec  # type: ignore[attr-defined]
        return wrapper

    if fn is not None:
        return decorate(fn)
    return decorate
