"""Approximate list prices (USD per 1M tokens) used to estimate LLM cost offline.

Prices change; unknown models cost ``None`` rather than a guess. Override or extend with
``tervic.core.pricing.PRICES["my-model"] = (input_per_m, output_per_m)``.
"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "claude-haiku": (1.00, 5.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}


def estimate_cost(model: str | None, input_tokens: int | None, output_tokens: int | None) -> float | None:
    if not model or (input_tokens is None and output_tokens is None):
        return None
    name = model.lower()
    best: tuple[float, float] | None = None
    best_len = 0
    for prefix, price in PRICES.items():
        if (name.startswith(prefix) or prefix in name) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    if best is None:
        return None
    return ((input_tokens or 0) * best[0] + (output_tokens or 0) * best[1]) / 1_000_000
