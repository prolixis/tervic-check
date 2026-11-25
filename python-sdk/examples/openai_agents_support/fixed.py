"""Fixed: the order tool returns the real order status, so the answer is grounded."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from agent import make_agent, run_agent  # noqa: E402

ORDERS = {"42": {"status": "shipped", "carrier": "DHL", "eta": "2026-11-22"}}


def lookup_order(order_id: str) -> dict[str, Any]:
    """Look up an order by id."""
    return ORDERS[order_id]


def run(question: str = "Where is my order 42?") -> str:
    return run_agent(make_agent(lookup_order), question)


if __name__ == "__main__":
    print(run())
