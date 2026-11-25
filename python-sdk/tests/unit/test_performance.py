"""Overhead per step: target < 1 ms p95 in prod mode and < 3 ms in dev mode (see docs/DECISIONS.md)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BENCH = Path(__file__).parents[2] / "scripts" / "benchmark.py"


def _bench() -> dict[str, Any]:
    """Run the benchmark in a clean subprocess: no framework threads from other tests compete."""
    out = subprocess.run([sys.executable, str(BENCH), "--steps", "1500"], capture_output=True, text=True,
                         check=True, timeout=300)
    result: dict[str, Any] = json.loads(out.stdout)
    return result


def test_step_overhead_within_targets() -> None:
    result = _bench()
    print(f"benchmark: {result}")
    assert result["prod"]["p95_ms"] < 1.0, result
    assert result["dev"]["p95_ms"] < 3.0, result


def test_import_time_under_150ms() -> None:
    code = "import time;t=time.perf_counter();import tervic;print((time.perf_counter()-t)*1000)"
    samples = []
    for _ in range(3):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        samples.append(float(out.stdout.strip()))
    assert min(samples) < 150, samples


def test_import_needs_only_core() -> None:
    code = (
        "import sys, tervic\n"
        "heavy = [m for m in ('langgraph', 'langchain_core', 'agents', 'crewai', 'opentelemetry', 'mcp', "
        "'pydantic', 'rich', 'typer') if m in sys.modules]\n"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
