"""Broken: the order tool swallows a timeout and returns {"error": "timeout"}; the agent carries on
and tells the customer their order shipped.

Expected: TV101 error-in-output and TV102 tool-error-ignored, root cause = `lookup_order`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from agent import make_agent, run_agent  # noqa: E402


def lookup_order(order_id: str) -> dict[str, Any]:
    """Look up an order by id."""
    try:
        raise TimeoutError("orders service did not answer in 5s")
    except TimeoutError:
        return {"error": "timeout"}  # Bug: the failure is swallowed and returned as data


def run(question: str = "Where is my order 42?") -> str:
    return run_agent(make_agent(lookup_order), question)


if __name__ == "__main__":
    print(run())
