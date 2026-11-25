"""``pytest --tervic``: run each test inside ``tervic.run(<test id>)`` and fail it if the verdict is fail.

Runs started by adapters during a test (for example a CrewAI kickoff, whose events arrive on
another thread) are attributed to the test as well. A summary section is added to the output.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from typing import Any

import pytest

_results: list[tuple[str, str, str, str]] = []
_lock = threading.Lock()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("tervic")
    group.addoption(
        "--tervic",
        action="store_true",
        default=False,
        help="Record each test as a Tervic run and fail tests whose run verdict is 'fail'.",
    )
    group.addoption(
        "--tervic-fail-on",
        default=None,
        choices=("error", "warn"),
        help="Override fail_on for this pytest session.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "tervic_skip: do not record this test with Tervic")
    if config.getoption("--tervic") and config.getoption("--tervic-fail-on"):
        from tervic.config import configure

        configure(fail_on=config.getoption("--tervic-fail-on"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, None]:
    if not item.config.getoption("--tervic") or item.get_closest_marker("tervic_skip"):
        yield
        return
    from tervic.api import flush
    from tervic.core.recorder import add_run_listener, remove_run_listener
    from tervic.core.run import RunScope

    started = time.time()
    extra: list[Any] = []

    def listener(run: Any) -> None:
        if run.started_ts >= started:
            extra.append(run)

    add_run_listener(listener)
    scope = RunScope(item.nodeid, tags={"pytest": "1"}, framework="pytest")
    scope.__enter__()
    outcome = yield
    exc = outcome.excinfo[1] if outcome.excinfo else None
    scope.__exit__(type(exc) if exc else None, exc, None)
    flush()
    remove_run_listener(listener)
    runs = [scope.run] if scope.run is not None and scope.run.events and len(scope.run.events) > 2 else []
    runs += [r for r in extra if r is not scope.run]
    failed = []
    for run in runs:
        verdict = run.verdict
        report = run.report
        summary = ""
        if report is not None and report.groups:
            group = report.groups[0]
            summary = (
                f"{group.root_cause_step_name or 'run'}: {group.title} [{', '.join(group.detector_ids)}]"
            )
        with _lock:
            _results.append((item.nodeid, run.run_id, verdict, summary))
        if verdict == "fail" and run.status != "crashed":
            failed.append((run, summary))
    if failed and exc is None:
        lines = [
            f"Tervic verdict FAIL for run {run.run_id} ({run.name}): {summary}" for run, summary in failed
        ]
        lines.append(
            f"Details: tervic show {failed[0][0].run_id}  |  fix prompt: tervic fix {failed[0][0].run_id}"
        )
        outcome.force_exception(AssertionError("\n".join(lines)))


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    if not config.getoption("--tervic"):
        return
    with _lock:
        results = list(_results)
    terminalreporter.section("tervic")
    if not results:
        terminalreporter.write_line("No agent runs were recorded.")
        return
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for _, _, verdict, _ in results:
        counts[verdict] = counts.get(verdict, 0) + 1
    terminalreporter.write_line(
        f"{len(results)} run(s): {counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail"
    )
    for nodeid, run_id, verdict, summary in results:
        if verdict != "pass":
            terminalreporter.write_line(f"  {verdict.upper():4} {nodeid} ({run_id}) {summary}")
