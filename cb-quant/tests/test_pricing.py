import math

import pytest

from cbquant.pricing import ConvertibleBond, Market, bond_floor, greeks, parity, price

PLAIN = ConvertibleBond(coupon_rate=0.02, coupon_freq=2, maturity_years=2.0, conversion_ratio=1.0)
MKT = Market(spot=80.0, vol=0.30, risk_free=0.03, credit_spread=0.02)


def test_bond_floor_discounts_coupons_and_face_at_risky_rate():
    expected = sum(1.0 * math.exp(-0.05 * t) for t in (0.5, 1.0, 1.5, 2.0)) + 100 * math.exp(-0.05 * 2.0)
    assert bond_floor(PLAIN, MKT) == pytest.approx(expected)


def test_parity_is_conversion_ratio_times_spot():
    assert parity(PLAIN, MKT) == pytest.approx(80.0)


def test_bond_with_no_conversion_right_prices_at_its_bond_floor():
    straight = ConvertibleBond(coupon_rate=0.02, coupon_freq=2, maturity_years=2.0, conversion_ratio=0.0)
    assert price(straight, MKT, steps=200) == pytest.approx(bond_floor(straight, MKT), abs=1e-6)


def test_price_is_at_least_parity_and_bond_floor():
    value = price(PLAIN, MKT)
    assert value >= parity(PLAIN, MKT)
    assert value >= bond_floor(PLAIN, MKT) - 1e-9


def test_deep_in_the_money_bond_trades_near_parity():
    rich_stock = Market(spot=400.0, vol=0.30, risk_free=0.03, credit_spread=0.02)
    assert price(PLAIN, rich_stock) == pytest.approx(400.0, rel=0.02)


def test_higher_volatility_raises_price():
    calm = Market(spot=80.0, vol=0.15, risk_free=0.03, credit_spread=0.02)
    wild = Market(spot=80.0, vol=0.45, risk_free=0.03, credit_spread=0.02)
    assert price(PLAIN, wild) > price(PLAIN, calm)


def test_wider_credit_spread_lowers_price():
    weak = Market(spot=80.0, vol=0.30, risk_free=0.03, credit_spread=0.08)
    assert price(PLAIN, weak) < price(PLAIN, MKT)


def test_issuer_call_caps_the_price():
    callable_bond = ConvertibleBond(
        coupon_rate=0.02, coupon_freq=2, maturity_years=2.0, conversion_ratio=1.0, call_price=105.0
    )
    assert price(callable_bond, MKT) < price(PLAIN, MKT)


def test_holder_put_supports_the_price():
    weak = Market(spot=40.0, vol=0.30, risk_free=0.03, credit_spread=0.10)
    puttable = ConvertibleBond(
        coupon_rate=0.02, coupon_freq=2, maturity_years=2.0, conversion_ratio=1.0, put_price=100.0, put_years=1.0
    )
    assert price(puttable, weak) > price(PLAIN, weak)


def test_delta_is_between_zero_and_conversion_ratio():
    g = greeks(PLAIN, MKT)
    assert 0.0 < g.delta < 1.0
    assert g.delta_pct == pytest.approx(g.delta / PLAIN.conversion_ratio)
    assert g.vega > 0.0


CALLABLE = ConvertibleBond(
    coupon_rate=0.0, coupon_freq=2, maturity_years=3.0, conversion_ratio=0.70, call_price=115.0, call_start_years=1.0
)


def test_callable_bond_price_never_falls_as_the_stock_rises():
    prices = [
        price(CALLABLE, Market(spot=float(spot), vol=0.40, risk_free=0.03, credit_spread=0.045))
        for spot in range(100, 141)
    ]
    assert all(later >= earlier - 1e-9 for earlier, later in zip(prices, prices[1:]))


def test_callable_bond_delta_stays_within_the_conversion_ratio():
    g = greeks(CALLABLE, Market(spot=120.0, vol=0.40, risk_free=0.03, credit_spread=0.045))
    assert 0.0 <= g.delta <= CALLABLE.conversion_ratio


def test_price_rejects_inputs_the_tree_cannot_represent():
    frozen = Market(spot=80.0, vol=0.001, risk_free=0.20, credit_spread=0.10)
    with pytest.raises(ValueError, match="volatility"):
        price(PLAIN, frozen)
