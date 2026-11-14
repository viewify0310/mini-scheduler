from cbquant.report import render_report

VALUATIONS = [
    {"bond_id": "CB-1", "issuer": "X", "market_price": 100.0, "model_price": 106.0, "parity": 80.0, "bond_floor": 90.0,
     "delta": 0.5, "delta_pct": 0.5, "vega": 0.4, "credit_spread": 0.02},
]
TICKETS = [
    {"bond_id": "CB-1", "issuer": "X", "action": "BUY", "market_price": 100.0, "cheapness": 0.06, "delta": 0.5,
     "credit_spread": 0.02, "face_amount": 200_000, "market_value": 200_000.0, "hedge_shares": -1000},
]


def test_report_lists_every_valued_bond_with_model_and_market_price():
    text = render_report(VALUATIONS, TICKETS, decision=None)
    assert "CB-1" in text
    assert "106.00" in text
    assert "100.00" in text


def test_report_says_data_is_synthetic_and_no_orders_were_sent():
    text = render_report(VALUATIONS, TICKETS, decision=None)
    assert "표본(가상) 데이터" in text
    assert "실제 주문은 전송되지 않았습니다" in text


def test_approved_decision_marks_tickets_as_approved_with_the_note():
    text = render_report(VALUATIONS, TICKETS, decision={"approved": True, "note": "헤지 먼저"})
    assert "승인" in text
    assert "헤지 먼저" in text
    assert "200,000" in text
    assert "-1,000" in text


def test_rejected_decision_marks_tickets_as_rejected():
    text = render_report(VALUATIONS, TICKETS, decision={"approved": False, "note": ""})
    assert "반려" in text


def test_report_without_tickets_says_there_was_nothing_to_trade():
    text = render_report(VALUATIONS, [], decision=None)
    assert "매매 대상 없음" in text
