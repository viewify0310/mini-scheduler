from cbquant.risk import RiskLimits, check, resize
from cbquant.signals import sized

LIMITS = RiskLimits(max_value_per_issuer=300_000, max_total_value=500_000, max_credit_spread=0.06)


def ticket(bond_id, issuer, face_amount, market_price=100.0, credit_spread=0.02, delta=0.5):
    base = {
        "bond_id": bond_id,
        "issuer": issuer,
        "action": "BUY",
        "market_price": market_price,
        "cheapness": 0.05,
        "delta": delta,
        "credit_spread": credit_spread,
    }
    return sized(base, face_amount)


def rules(breaches):
    return sorted(b["rule"] for b in breaches)


def test_book_within_limits_has_no_breaches():
    assert check([ticket("A", "X", 200_000), ticket("B", "Y", 200_000)], LIMITS) == []


def test_credit_spread_above_limit_is_a_breach():
    breaches = check([ticket("A", "X", 100_000, credit_spread=0.09)], LIMITS)
    assert rules(breaches) == ["credit_spread"]
    assert breaches[0]["subject"] == "A"


def test_issuer_limit_adds_up_bonds_of_the_same_issuer():
    breaches = check([ticket("A1", "X", 200_000), ticket("A2", "X", 200_000)], LIMITS)
    assert rules(breaches) == ["issuer_limit"]
    assert breaches[0]["subject"] == "X"
    assert breaches[0]["value"] == 400_000


def test_total_limit_adds_up_the_whole_book():
    book = [ticket("A", "X", 250_000), ticket("B", "Y", 250_000), ticket("C", "Z", 250_000)]
    assert rules(check(book, LIMITS)) == ["total_limit"]


def test_resize_drops_bonds_over_the_credit_spread_limit():
    resized = resize([ticket("BAD", "X", 100_000, credit_spread=0.09), ticket("OK", "Y", 100_000)], LIMITS)
    assert [t["bond_id"] for t in resized] == ["OK"]


def test_resize_scales_an_issuer_down_to_its_limit():
    resized = resize([ticket("A1", "X", 200_000), ticket("A2", "X", 200_000)], LIMITS)
    assert [t["face_amount"] for t in resized] == [150_000, 150_000]


def test_resize_rescales_the_stock_hedge_with_the_position():
    (resized,) = resize([ticket("A", "X", 400_000, delta=0.5)], LIMITS)
    assert resized["face_amount"] == 300_000
    assert resized["hedge_shares"] == -1500


def test_resized_book_always_passes_the_check():
    book = [
        ticket("A1", "X", 400_000, market_price=104.5),
        ticket("A2", "X", 250_000, market_price=97.3),
        ticket("B", "Y", 350_000, market_price=111.2),
        ticket("C", "Z", 320_000, market_price=101.1, credit_spread=0.07),
        ticket("D", "W", 290_000, market_price=99.9),
    ]
    assert check(book, LIMITS) != []
    assert check(resize(book, LIMITS), LIMITS) == []
