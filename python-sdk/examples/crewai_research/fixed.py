"""Fixed: the agent searches once, then answers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent import run_crew  # noqa: E402


def run(topic: str = "tervic") -> str:
    return run_crew("answering", topic)


if __name__ == "__main__":
    print(run())
