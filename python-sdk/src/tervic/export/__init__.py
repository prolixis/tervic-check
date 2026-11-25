"""Exporters send finished runs somewhere else (OpenTelemetry, a webhook, a future cloud backend).

Any object with an ``export(events, report)`` method is an exporter. Register it with
``tervic.add_exporter(obj)``. Exporters run in Tervic's background thread after analysis, never in
the agent's request path, and an exporter error never affects the agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tervic.analyze.engine import Report
    from tervic.core.events import Event


@runtime_checkable
class Exporter(Protocol):
    def export(self, events: list[Event], report: Report) -> None:
        """Called once per kept run, after analysis, in the background thread."""
        ...


__all__ = ["Exporter"]
