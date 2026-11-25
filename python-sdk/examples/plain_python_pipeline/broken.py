"""Broken: `normalize` rebuilds the state and drops `customer_id`. Nothing raises: the customer lookup
finds nobody and the reply goes out addressed to "there" on the "unknown" plan.

Expected: TV002 dropped-field, root cause = `normalize`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import tervic  # noqa: E402
from pipeline import handle  # noqa: E402


@tervic.step
def normalize(state: dict[str, Any]) -> dict[str, Any]:
    # Bug: builds a new dict and forgets customer_id
    return {"text": state["text"].strip(), "topic": state["topic"]}


def run(ticket: dict[str, Any] | None = None) -> str:
    return handle(ticket or {"customer_id": "c-17", "text": "  Where is my invoice? "}, normalize)


if __name__ == "__main__":
    print(run())
