"""Measure Tervic's overhead per instrumented step and print JSON.

Usage:  python scripts/benchmark.py [--steps 2000]

Runs in a temporary folder so it never touches your .tervic store. Overhead = time of an
instrumented call minus time of the same plain call, measured back to back. Results are recorded in
docs/DECISIONS.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from typing import Any


def measure(mode: str, steps: int) -> dict[str, float]:
    import tervic
    from tervic.config import reset_config

    reset_config()
    tervic.configure(mode=mode, sample_rate=0.1 if mode == "prod" else 1.0)

    def plain(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "n": state["n"] + 1, "text": "x" * 200}

    instrumented = tervic.step(name="work")(plain)
    state = {"n": 0, "docs": [{"id": i, "text": "doc text " * 10} for i in range(5)]}
    overhead: list[float] = []
    with tervic.run(f"bench-{mode}"):
        for _ in range(50):  # warm-up: imports, caches, writer thread
            instrumented(state)
        for _ in range(steps):
            t0 = time.perf_counter()
            plain(state)
            t1 = time.perf_counter()
            instrumented(state)
            t2 = time.perf_counter()
            overhead.append(((t2 - t1) - (t1 - t0)) * 1000)
    tervic.flush()
    overhead.sort()
    return {
        "p50_ms": round(statistics.median(overhead), 4),
        "p95_ms": round(overhead[int(len(overhead) * 0.95)], 4),
        "p99_ms": round(overhead[int(len(overhead) * 0.99)], 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=2000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        os.chdir(folder)
        result = {
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
            "steps": args.steps,
            "prod": measure("prod", args.steps),
            "dev": measure("dev", args.steps),
        }
        os.chdir(os.path.dirname(folder))
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
