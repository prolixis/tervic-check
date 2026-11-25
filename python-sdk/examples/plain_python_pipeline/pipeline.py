"""A plain-Python support pipeline, shared by broken.py and fixed.py.

Steps are plain functions decorated with ``@tervic.step``; the whole run is ``tervic.run(...)``.
The "LLM" is a recorded reply from ``cassette.json`` so the example runs offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tervic

HERE = Path(__file__).parent
CUSTOMERS = {"c-17": {"name": "Ada", "plan": "pro"}}
REPLY = json.loads((HERE / "cassette.json").read_text(encoding="utf-8"))["reply"]


@tervic.step
def parse_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    return {**ticket, "topic": "billing" if "invoice" in ticket["text"].lower() else "general"}


@tervic.step
def lookup_customer(state: dict[str, Any]) -> dict[str, Any]:
    customer = CUSTOMERS.get(state.get("customer_id") or "", {})
    return {**state, "customer": customer}


@tervic.step(kind="llm")
def draft_reply(state: dict[str, Any]) -> dict[str, Any]:
    name = state["customer"].get("name", "there")
    return {**state, "reply": REPLY.format(name=name, plan=state["customer"].get("plan", "unknown"))}


def handle(ticket: dict[str, Any], normalize: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    with tervic.run("support-pipeline", input=ticket) as run:
        state = parse_ticket(ticket)
        state = normalize(state)
        state = lookup_customer(state)
        state = draft_reply(state)
        run.set_output(state["reply"])
    return str(state["reply"])
