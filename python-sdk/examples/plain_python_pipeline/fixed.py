"""Fixed: `normalize` keeps every field it does not change."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import tervic  # noqa: E402
from pipeline import handle  # noqa: E402


@tervic.step
def normalize(state: dict[str, Any]) -> dict[str, Any]:
    return {**state, "text": state["text"].strip()}


def run(ticket: dict[str, Any] | None = None) -> str:
    return handle(ticket or {"customer_id": "c-17", "text": "  Where is my invoice? "}, normalize)


if __name__ == "__main__":
    print(run())
