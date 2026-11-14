"""Load a bond universe from CSV and value each row."""

from __future__ import annotations

import csv
from pathlib import Path

from .pricing import ConvertibleBond, Market, bond_floor, greeks, parity, price

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_universe.csv"

_TEXT_COLUMNS = {"bond_id", "issuer"}


def load_universe(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [
            {key: value if key in _TEXT_COLUMNS else (float(value) if value.strip() else None) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def value_row(row: dict) -> dict:
    bond = ConvertibleBond(
        coupon_rate=row["coupon_rate"],
        coupon_freq=int(row["coupon_freq"]),
        maturity_years=row["maturity_years"],
        conversion_ratio=row["conversion_ratio"],
        call_price=row["call_price"],
        call_start_years=row["call_start_years"] or 0.0,
        put_price=row["put_price"],
        put_years=row["put_years"],
    )
    market = Market(
        spot=row["spot"],
        vol=row["vol"],
        risk_free=row["risk_free"],
        credit_spread=row["credit_spread"],
        dividend_yield=row["dividend_yield"] or 0.0,
    )
    g = greeks(bond, market)
    return {
        "bond_id": row["bond_id"],
        "issuer": row["issuer"],
        "market_price": row["market_price"],
        "model_price": price(bond, market),
        "parity": parity(bond, market),
        "bond_floor": bond_floor(bond, market),
        "delta": g.delta,
        "delta_pct": g.delta_pct,
        "vega": g.vega,
        "credit_spread": row["credit_spread"],
    }
