import pytest

from cbquant.signals import build_tickets, cheapness, classify


def valuation(bond_id="CB-1", market_price=100.0, model_price=106.0, delta=0.5, issuer="ISS-1", credit_spread=0.02):
    return {
        "bond_id": bond_id,
        "issuer": issuer,
        "market_price": market_price,
        "model_price": model_price,
        "delta": delta,
        "credit_spread": credit_spread,
    }


def test_cheapness_is_model_over_market_minus_one():
    assert cheapness(market_price=100.0, model_price=106.0) == pytest.approx(0.06)
    assert cheapness(market_price=100.0, model_price=95.0) == pytest.approx(-0.05)


def test_classify_uses_threshold_on_both_sides():
    assert classify(0.031, threshold=0.03) == "BUY"
    assert classify(-0.031, threshold=0.03) == "AVOID"
    assert classify(0.029, threshold=0.03) == "HOLD"
    assert classify(-0.029, threshold=0.03) == "HOLD"


def test_only_cheap_bonds_become_tickets():
    tickets = build_tickets(
        [valuation("CHEAP", 100.0, 106.0), valuation("FAIR", 100.0, 101.0), valuation("RICH", 100.0, 94.0)],
        target_value=200_000,
        threshold=0.03,
    )
    assert [t["bond_id"] for t in tickets] == ["CHEAP"]


def test_ticket_is_sized_in_whole_lots_within_target_value():
    (ticket,) = build_tickets([valuation(market_price=103.0, model_price=110.0)], target_value=200_000, threshold=0.03)
    assert ticket["face_amount"] == 194_000  # 200,000 / 1.03 = 194,174 -> whole 1,000 lots
    assert ticket["market_value"] == pytest.approx(194_000 * 1.03)
    assert ticket["market_value"] <= 200_000


def test_ticket_carries_a_short_stock_hedge_sized_by_delta():
    (ticket,) = build_tickets([valuation(market_price=100.0, model_price=106.0, delta=0.5)], target_value=200_000, threshold=0.03)
    assert ticket["hedge_shares"] == -1000  # 0.5 shares per 100 face x 200,000 face


def test_tickets_are_ordered_cheapest_first():
    tickets = build_tickets(
        [valuation("A", 100.0, 104.0), valuation("B", 100.0, 109.0)], target_value=100_000, threshold=0.03
    )
    assert [t["bond_id"] for t in tickets] == ["B", "A"]
