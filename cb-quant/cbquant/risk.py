"""Risk limits for a book of trade tickets, and how to shrink a book to fit."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .signals import sized


@dataclass(frozen=True)
class RiskLimits:
    max_value_per_issuer: float
    max_total_value: float
    max_credit_spread: float


def _value_by_issuer(tickets: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for t in tickets:
        totals[t["issuer"]] += t["market_value"]
    return totals


def check(tickets: list[dict], limits: RiskLimits) -> list[dict]:
    """Return every limit the book breaks; an empty list means it passes."""
    breaches = []
    for t in tickets:
        if t["credit_spread"] > limits.max_credit_spread:
            breaches.append(
                {"rule": "credit_spread", "subject": t["bond_id"], "value": t["credit_spread"], "limit": limits.max_credit_spread}
            )
    for issuer, value in _value_by_issuer(tickets).items():
        if value > limits.max_value_per_issuer:
            breaches.append({"rule": "issuer_limit", "subject": issuer, "value": value, "limit": limits.max_value_per_issuer})
    total = sum(t["market_value"] for t in tickets)
    if total > limits.max_total_value:
        breaches.append({"rule": "total_limit", "subject": "book", "value": total, "limit": limits.max_total_value})
    return breaches


def resize(tickets: list[dict], limits: RiskLimits) -> list[dict]:
    """Drop bonds that are too risky, then scale positions down to the limits."""
    kept = [t for t in tickets if t["credit_spread"] <= limits.max_credit_spread]

    by_issuer = _value_by_issuer(kept)
    kept = [
        sized(t, t["face_amount"] * min(1.0, limits.max_value_per_issuer / by_issuer[t["issuer"]])) for t in kept
    ]

    total = sum(t["market_value"] for t in kept)
    if total > limits.max_total_value:
        kept = [sized(t, t["face_amount"] * limits.max_total_value / total) for t in kept]

    return [t for t in kept if t["face_amount"] > 0]
