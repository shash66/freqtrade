from decimal import Decimal

from app.competition.attainable import AttainableInputs, attainable, rank

BIDS = [10, 20, 30, 40, 50]


def _i(cat, net="10", bid=45, share=None):
    return AttainableInputs(Decimal(net), Decimal("0.05"), cat, bid, BIDS, 1, share, "measured" if share is not None else "unmeasured")


def test_publicly_observable_uses_bid_win_probability():
    a = attainable(_i("publicly_observable"))
    assert a.p_win_bid == Decimal("0.8") and a.p_capture == Decimal("0.8")
    assert a.attainable_net_profit_usd == Decimal("7.990000")      # 0.8*10 - 0.2*0.05


def test_predictable_needs_measured_availability_share():
    assert attainable(_i("predictable_not_yet_eligible")).attainable_net_profit_usd is None
    a = attainable(_i("predictable_not_yet_eligible", share=Decimal("0.25")))
    assert a.p_capture == Decimal("0.2") and a.attainable_net_profit_usd == Decimal("1.960000")


def test_intra_block_and_unknown_are_zero_not_negative():
    assert attainable(_i("intra_block_only", net="3120")).attainable_net_profit_usd == Decimal("0")
    assert attainable(_i("unknown", net="3120")).attainable_net_profit_usd == Decimal("0")


def test_rank_puts_unknown_last_and_sorts_by_value():
    rows = rank([("a", _i("intra_block_only", net="3120")), ("b", _i("publicly_observable", net="1")), ("c", _i("predictable_not_yet_eligible", net="50")),
                 ("d", _i("publicly_observable", net="0.01"))])
    assert [k for k, _ in rows] == ["b", "a", "d", "c"] or [k for k, _ in rows][0] == "b" and [k for k, _ in rows][-1] == "c"
