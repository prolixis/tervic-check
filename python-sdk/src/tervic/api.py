"""Programmatic analysis: ``tervic.check(run_id=None) -> Report``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tervic.config import TervicConfig, get_config
from tervic.core.recorder import get_writer
from tervic.store.local import LocalStore

if TYPE_CHECKING:
    from tervic.analyze.engine import Report


def flush(timeout: float = 10.0) -> bool:
    """Wait until everything Tervic has queued (including async framework events) is on disk."""
    from tervic.core.recorder import run_flush_hooks

    run_flush_hooks(timeout)
    return get_writer().flush(timeout)


def store_for(config: TervicConfig | None = None) -> LocalStore:
    return LocalStore((config or get_config()).store_path())


def check(run_id: str | None = None, *, config: TervicConfig | None = None, write: bool = True) -> Report:
    """Analyze a recorded run (default: the latest) and return its Report.

    Re-runs every detector against the stored events with the current configuration, compares with
    the baseline for that run name if one exists, and (by default) refreshes ``report.json`` and the
    run index.
    """
    from tervic.analyze.engine import analyze
    from tervic.store.local import RunRow

    config = config or get_config()
    flush()
    store = store_for(config)
    resolved = store.resolve(run_id)
    events = store.load_events(resolved)
    report = analyze(events, config, store)
    if write:
        store.write_json(resolved, "report.json", report.model_dump(mode="json"))
        row = store.get_row(resolved)
        started_ts = row.started_ts if row else (events[0].ts if events else None)
        store.upsert(
            RunRow(
                run_id=resolved,
                name=report.run_name,
                framework=report.framework,
                verdict=report.verdict,
                status=report.status,
                started_at=report.started_at,
                started_ts=started_ts,
                duration_ms=report.stats.duration_ms,
                cost_usd=report.stats.cost_usd,
                git_sha=report.git_sha,
                tags=report.tags,
                errors=report.counts.get("error", 0),
                warnings=report.counts.get("warn", 0),
            )
        )
    return report
