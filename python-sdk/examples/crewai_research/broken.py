"""Broken: the agent keeps calling web_search with the same query instead of answering.

Expected: TV401 loop, root cause = the `research` task.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent import run_crew  # noqa: E402


def run(topic: str = "tervic") -> str:
    return run_crew("looping", topic)


if __name__ == "__main__":
    print(run())
