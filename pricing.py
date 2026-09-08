"""Approximate model prices, used for the cost estimates in the UI.

Prices are USD per 1M tokens as (input, output) and drift over time — an unknown
model simply reports no cost rather than a wrong one.
"""

from __future__ import annotations

MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o":          (2.50,  10.00),
    "gpt-4o-mini":     (0.15,   0.60),
    "gpt-4.1":         (2.00,   8.00),
    "gpt-4.1-mini":    (0.40,   1.60),
    "gpt-4-turbo":    (10.00,  30.00),
    "gpt-4":          (30.00,  60.00),
    "gpt-3.5-turbo":   (0.50,   1.50),
    "o1":             (15.00,  60.00),
    "o1-mini":         (3.00,  12.00),
    "o3-mini":         (1.10,   4.40),
    "o3":             (10.00,  40.00),
    "o4-mini":         (1.10,   4.40),
}


def estimate_cost(model: str, prompt: int, completion: int) -> float | None:
    """USD estimate for one exchange, or None when the model is unknown.

    Model names are matched by substring and the *longest* match wins — otherwise
    "gpt-4o-mini" would be priced as "gpt-4o" and "o1-mini" as "o1".
    """
    model_lc = (model or "").lower()
    best_key: str | None = None
    for key in MODEL_PRICES:
        if key in model_lc and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return None
    in_p, out_p = MODEL_PRICES[best_key]
    return (prompt * in_p + completion * out_p) / 1_000_000
