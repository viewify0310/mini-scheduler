import pytest

from cbquant.pricing import ConvertibleBond, Market, bond_floor, greeks, price
from cbquant.universe import SAMPLE_PATH, load_universe, value_row

CSV = """bond_id,issuer,spot,vol,risk_free,credit_spread,dividend_yield,coupon_rate,coupon_freq,maturity_years,conversion_ratio,call_price,call_start_years,put_price,put_years,market_price
CB-1,ISS-1,80,0.30,0.03,0.02,0,0.02,2,2,1.0,,,,,100.0
CB-2,ISS-2,50,0.25,0.03,0.04,0.01,0.01,2,3,1.6,110,1,100,2,98.5
"""


@pytest.fixture
def rows(tmp_path):
    path = tmp_path / "universe.csv"
    path.write_text(CSV)
    return load_universe(path)


def test_load_universe_reads_numbers_as_floats(rows):
    assert rows[0]["bond_id"] == "CB-1"
    assert rows[0]["spot"] == 80.0
    assert rows[1]["conversion_ratio"] == 1.6


def test_blank_call_and_put_terms_mean_the_bond_has_none(rows):
    assert rows[0]["call_price"] is None
    assert rows[0]["put_years"] is None
    assert rows[1]["call_price"] == 110.0


def test_value_row_prices_the_bond_described_by_the_row(rows):
    bond = ConvertibleBond(coupon_rate=0.02, coupon_freq=2, maturity_years=2.0, conversion_ratio=1.0)
    market = Market(spot=80.0, vol=0.30, risk_free=0.03, credit_spread=0.02)
    valuation = value_row(rows[0])
    assert valuation["model_price"] == pytest.approx(price(bond, market))
    assert valuation["bond_floor"] == pytest.approx(bond_floor(bond, market))
    assert valuation["parity"] == pytest.approx(80.0)
    assert valuation["delta"] == pytest.approx(greeks(bond, market).delta)
    assert valuation["market_price"] == 100.0
    assert valuation["issuer"] == "ISS-1"
    assert valuation["credit_spread"] == 0.02


def test_value_row_applies_call_and_put_terms(rows):
    with_terms = value_row(rows[1])["model_price"]
    without_terms = value_row({**rows[1], "call_price": None, "put_price": None, "put_years": None})["model_price"]
    assert with_terms != pytest.approx(without_terms)


def test_bundled_sample_universe_loads_and_prices():
    rows = load_universe(SAMPLE_PATH)
    assert len(rows) >= 6
    for row in rows:
        assert value_row(row)["model_price"] > 0
