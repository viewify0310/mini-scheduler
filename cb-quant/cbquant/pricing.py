"""Convertible bond valuation on a binomial stock tree.

Credit risk is a jump to default with zero recovery: the issuer defaults at
a rate equal to the credit spread, at which point both the bond and the
stock are worthless. Every value is therefore discounted at risk-free plus
spread, and the surviving stock drifts faster by the spread to compensate.

Prices are per 100 of face. Rates are continuously compounded decimals.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math


@dataclass(frozen=True)
class ConvertibleBond:
    coupon_rate: float
    maturity_years: float
    conversion_ratio: float  # shares received per 100 of face
    coupon_freq: int = 2
    face: float = 100.0
    call_price: float | None = None  # issuer may redeem at this price...
    call_start_years: float = 0.0  # ...from this time on
    put_price: float | None = None  # holder may sell back at this price...
    put_years: float | None = None  # ...on this date only


@dataclass(frozen=True)
class Market:
    spot: float
    vol: float
    risk_free: float
    credit_spread: float
    dividend_yield: float = 0.0


@dataclass(frozen=True)
class Greeks:
    delta: float  # change in bond price per 1.0 move in the stock
    delta_pct: float  # delta as a fraction of the conversion ratio (0..1)
    vega: float  # change in bond price per 1 vol point (0.01)


def _coupon_times(bond: ConvertibleBond) -> list[float]:
    if bond.coupon_rate == 0 or bond.coupon_freq == 0:
        return []
    period = 1.0 / bond.coupon_freq
    count = round(bond.maturity_years * bond.coupon_freq)
    return [bond.maturity_years - k * period for k in range(count) if bond.maturity_years - k * period > 1e-9]


def _coupon_amount(bond: ConvertibleBond) -> float:
    return bond.face * bond.coupon_rate / bond.coupon_freq if bond.coupon_freq else 0.0


def parity(bond: ConvertibleBond, market: Market) -> float:
    """Value of converting into stock right now."""
    return bond.conversion_ratio * market.spot


def bond_floor(bond: ConvertibleBond, market: Market) -> float:
    """Value as a straight bond with no conversion right."""
    rate = market.risk_free + market.credit_spread
    coupons = sum(_coupon_amount(bond) * math.exp(-rate * t) for t in _coupon_times(bond))
    return coupons + bond.face * math.exp(-rate * bond.maturity_years)


def price(bond: ConvertibleBond, market: Market, steps: int = 150) -> float:
    dt = bond.maturity_years / steps
    up = math.exp(market.vol * math.sqrt(dt))
    down = 1.0 / up
    growth = math.exp((market.risk_free - market.dividend_yield + market.credit_spread) * dt)
    p_up = (growth - down) / (up - down)
    if not 0.0 < p_up < 1.0:
        raise ValueError(
            f"volatility {market.vol} is too low for these rates on a {steps}-step tree; raise steps or check inputs"
        )
    discount = math.exp(-(market.risk_free + market.credit_spread) * dt)

    coupon = _coupon_amount(bond)
    coupon_steps = {round(t / dt) for t in _coupon_times(bond)}
    call_from = math.ceil(bond.call_start_years / dt - 1e-9) if bond.call_price is not None else None
    put_step = round(bond.put_years / dt) if bond.put_price is not None and bond.put_years is not None else None

    def converted(step: int, ups: int) -> float:
        return bond.conversion_ratio * market.spot * up**ups * down ** (step - ups)

    # At maturity the holder takes the better of shares and redemption.
    redemption = bond.face + (coupon if steps in coupon_steps else 0.0)
    values = [max(converted(steps, ups), redemption) for ups in range(steps + 1)]

    for step in range(steps - 1, -1, -1):
        for ups in range(step + 1):
            value = discount * (p_up * values[ups + 1] + (1 - p_up) * values[ups])
            if call_from is not None and step >= call_from:
                value = min(value, bond.call_price)  # issuer redeems when the bond is worth more
            if step == put_step:
                value = max(value, bond.put_price)
            value = max(value, converted(step, ups))  # holder can always convert instead
            if step in coupon_steps and step > 0:
                value += coupon
            values[ups] = value

    return values[0]


def greeks(bond: ConvertibleBond, market: Market, steps: int = 150) -> Greeks:
    bump = market.spot * 0.01
    base = price(bond, market, steps)
    higher = price(bond, replace(market, spot=market.spot + bump), steps)
    lower = price(bond, replace(market, spot=market.spot - bump), steps)
    more_vol = price(bond, replace(market, vol=market.vol + 0.01), steps)

    delta = (higher - lower) / (2 * bump)
    return Greeks(
        delta=delta,
        delta_pct=delta / bond.conversion_ratio if bond.conversion_ratio else 0.0,
        vega=more_vol - base,
    )
