"""Turn valuations into sized trade tickets.

A bond is a BUY when the model values it above the market by more than the
threshold. Each BUY is paired with a short stock hedge sized by its delta.
"""

from __future__ import annotations

LOT = 1_000  # face amount is traded in whole lots


def cheapness(market_price: float, model_price: float) -> float:
    return model_price / market_price - 1.0


def classify(cheap: float, threshold: float) -> str:
    if cheap > threshold:
        return "BUY"
    if cheap < -threshold:
        return "AVOID"
    return "HOLD"


def sized(ticket: dict, face_amount: float) -> dict:
    """Return the ticket at a new size, with value and hedge recomputed."""
    face_amount = int(face_amount // LOT * LOT)
    return {
        **ticket,
        "face_amount": face_amount,
        "market_value": face_amount * ticket["market_price"] / 100.0,
        "hedge_shares": -round(ticket["delta"] * face_amount / 100.0),
    }


def build_tickets(valuations: list[dict], target_value: float, threshold: float) -> list[dict]:
    tickets = []
    for v in valuations:
        cheap = cheapness(v["market_price"], v["model_price"])
        if classify(cheap, threshold) != "BUY":
            continue
        ticket = {
            "bond_id": v["bond_id"],
            "issuer": v["issuer"],
            "action": "BUY",
            "market_price": v["market_price"],
            "cheapness": cheap,
            "delta": v["delta"],
            "credit_spread": v["credit_spread"],
        }
        tickets.append(sized(ticket, target_value / (v["market_price"] / 100.0)))
    return sorted(tickets, key=lambda t: t["cheapness"], reverse=True)
