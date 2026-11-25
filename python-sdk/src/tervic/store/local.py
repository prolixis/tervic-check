"""Local storage: ``.tervic/runs/<run_id>/events.jsonl`` + ``report.json`` and ``index.sqlite``."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tervic.core.events import Event, parse_event

GITIGNORE = """\
# Created by Tervic. Run data stays local; baselines may be committed.
*
!.gitignore
!baselines/
!baselines/**
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    framework TEXT,
    verdict TEXT,
    status TEXT,
    started_at TEXT,
    started_ts REAL,
    duration_ms REAL,
    cost_usd REAL,
    git_sha TEXT,
    tags TEXT,
    errors INTEGER DEFAULT 0,
    warnings INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_started ON runs(started_ts);
"""


class RunRow(BaseModel):
    run_id: str
    name: str
    framework: str | None = None
    verdict: str | None = None
    status: str | None = None
    started_at: str | None = None
    started_ts: float | None = None
    duration_ms: float | None = None
    cost_usd: float | None = None
    git_sha: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    errors: int = 0
    warnings: int = 0


class RunNotFound(LookupError):
    pass


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._ready = False

    # ------------------------------------------------------------------ layout
    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def baselines_dir(self) -> Path:
        return self.root / "baselines"

    @property
    def index_path(self) -> Path:
        return self.root / "index.sqlite"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def ensure(self) -> None:
        with self._lock:
            if self._ready and self.root.is_dir():
                return
            self.root.mkdir(parents=True, exist_ok=True)
            gitignore = self.root / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(GITIGNORE, encoding="utf-8")
            self.runs_dir.mkdir(exist_ok=True)
            self.baselines_dir.mkdir(exist_ok=True)
            with self._db() as db:
                db.executescript(_SCHEMA)
            self._ready = True

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.index_path, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ writing
    def append_lines(self, run_id: str, lines: list[str]) -> None:
        if not lines:
            return
        self.ensure()
        folder = self.run_dir(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def write_lines(self, run_id: str, lines: list[str]) -> None:
        self.ensure()
        folder = self.run_dir(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "events.jsonl.tmp"
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(folder / "events.jsonl")

    def write_json(self, run_id: str, filename: str, data: Any) -> Path:
        self.ensure()
        folder = self.run_dir(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
        return path

    def upsert(self, row: RunRow) -> None:
        self.ensure()
        with self._lock, self._db() as db:
            db.execute(
                """INSERT INTO runs (run_id, name, framework, verdict, status, started_at, started_ts,
                       duration_ms, cost_usd, git_sha, tags, errors, warnings)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       name=excluded.name, framework=excluded.framework, verdict=excluded.verdict,
                       status=excluded.status, started_at=excluded.started_at,
                       started_ts=excluded.started_ts, duration_ms=excluded.duration_ms,
                       cost_usd=excluded.cost_usd, git_sha=excluded.git_sha, tags=excluded.tags,
                       errors=excluded.errors, warnings=excluded.warnings""",
                (
                    row.run_id,
                    row.name,
                    row.framework,
                    row.verdict,
                    row.status,
                    row.started_at,
                    row.started_ts,
                    row.duration_ms,
                    row.cost_usd,
                    row.git_sha,
                    json.dumps(row.tags),
                    row.errors,
                    row.warnings,
                ),
            )

    # ------------------------------------------------------------------ reading
    def list_runs(
        self,
        limit: int = 20,
        verdict: str | None = None,
        since_ts: float | None = None,
        name: str | None = None,
    ) -> list[RunRow]:
        if not self.index_path.exists():
            return []
        query = (
            "SELECT run_id, name, framework, verdict, status, started_at, started_ts, duration_ms, "
            "cost_usd, git_sha, tags, errors, warnings FROM runs WHERE 1=1"
        )
        params: list[Any] = []
        if verdict:
            query += " AND verdict = ?"
            params.append(verdict)
        if since_ts is not None:
            query += " AND started_ts >= ?"
            params.append(since_ts)
        if name:
            query += " AND name = ?"
            params.append(name)
        query += " ORDER BY started_ts DESC, run_id DESC LIMIT ?"
        params.append(limit)
        with self._db() as db:
            rows = db.execute(query, params).fetchall()
        result = []
        for r in rows:
            result.append(
                RunRow(
                    run_id=r[0],
                    name=r[1],
                    framework=r[2],
                    verdict=r[3],
                    status=r[4],
                    started_at=r[5],
                    started_ts=r[6],
                    duration_ms=r[7],
                    cost_usd=r[8],
                    git_sha=r[9],
                    tags=json.loads(r[10] or "{}"),
                    errors=r[11] or 0,
                    warnings=r[12] or 0,
                )
            )
        return result

    def get_row(self, run_id: str) -> RunRow | None:
        for row in self.list_runs(limit=100_000):
            if row.run_id == run_id:
                return row
        return None

    def resolve(self, ref: str | None) -> str:
        """``None``/``"last"`` → newest run; full id; or a unique id prefix."""
        if ref is None or ref == "last":
            rows = self.list_runs(limit=1)
            if not rows:
                raise RunNotFound("No runs recorded yet. Run your agent with Tervic attached first.")
            return rows[0].run_id
        if self.run_dir(ref).is_dir():
            return ref
        matches = [p.name for p in self.runs_dir.glob(f"{ref}*")] if self.runs_dir.is_dir() else []
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RunNotFound(f"Run id prefix '{ref}' is ambiguous ({len(matches)} matches).")
        raise RunNotFound(f"Run '{ref}' not found in {self.root}.")

    def load_events(self, run_id: str) -> list[Event]:
        return load_events_file(self.run_dir(run_id) / "events.jsonl")

    def load_json(self, run_id: str, filename: str) -> Any:
        path = self.run_dir(run_id) / filename
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ cleanup
    def delete_run(self, run_id: str) -> None:
        shutil.rmtree(self.run_dir(run_id), ignore_errors=True)
        if self.index_path.exists():
            with self._lock, self._db() as db:
                db.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def gc(self, keep: int) -> list[str]:
        rows = self.list_runs(limit=1_000_000)
        doomed = [row.run_id for row in rows[keep:]]
        indexed = {row.run_id for row in rows}
        if self.runs_dir.is_dir():
            doomed += [p.name for p in self.runs_dir.iterdir() if p.is_dir() and p.name not in indexed]
        for run_id in doomed:
            self.delete_run(run_id)
        return doomed

    # ------------------------------------------------------------------ baselines
    def baseline_dir(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        return self.baselines_dir / safe

    def save_baseline(self, name: str, run_id: str) -> Path:
        self.ensure()
        source = self.run_dir(run_id)
        if not (source / "events.jsonl").is_file():
            raise RunNotFound(f"Run '{run_id}' has no recorded events.")
        target = self.baseline_dir(name)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copy2(source / "events.jsonl", target / "events.jsonl")
        row = self.get_row(run_id)
        meta = {"name": name, "run_id": run_id, "run_name": row.name if row else None}
        (target / "baseline.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return target

    def list_baselines(self) -> list[dict[str, Any]]:
        if not self.baselines_dir.is_dir():
            return []
        out = []
        for folder in sorted(self.baselines_dir.iterdir()):
            meta_file = folder / "baseline.json"
            if folder.is_dir() and meta_file.is_file():
                out.append(json.loads(meta_file.read_text(encoding="utf-8")))
        return out

    def remove_baseline(self, name: str) -> bool:
        target = self.baseline_dir(name)
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True

    def baseline_events(self, name: str) -> list[Event] | None:
        path = self.baseline_dir(name) / "events.jsonl"
        return load_events_file(path) if path.is_file() else None

    def baseline_for_run_name(self, run_name: str) -> tuple[str, list[Event]] | None:
        for meta in self.list_baselines():
            if meta.get("run_name") == run_name or meta.get("name") == run_name:
                events = self.baseline_events(str(meta["name"]))
                if events is not None:
                    return str(meta["name"]), events
        return None


def load_events_file(path: Path) -> list[Event]:
    if not path.is_file():
        raise RunNotFound(f"No events file at {path}.")
    events: list[Event] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                events.append(parse_event(line))
            except Exception:
                continue  # a partially written trailing line after a crash
    events.sort(key=lambda e: (e.ts, e.seq))
    return events
