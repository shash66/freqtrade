import random
from decimal import Decimal

from app.competition.compare import EventRow, _ready_prob, availability, build_dataset
from app.protocols.compound_v3 import FACTOR_SCALE, quote_collateral_bonus_bps


def _row(cat, reveal=None, lead=None, net=None):
    return EventRow("0xa", 0, 1, cat, lead, reveal, Decimal(net) if net is not None else None, 1)


def test_availability_rules():
    assert availability(_row("publicly_observable"))[0] == 1.0
    assert availability(_row("predictable_not_yet_eligible", reveal=0))[0] == 1.0      # price public at the winning block: a race
    assert availability(_row("predictable_not_yet_eligible", reveal=None))[0] == 1.0   # time-only eligibility
    assert availability(_row("predictable_not_yet_eligible", reveal=30))[0] == 0.0     # taken inside the exclusive window
    assert availability(_row("intra_block_only"))[0] == 0.0 and availability(_row("unknown"))[0] == 0.0


def test_ready_probability_is_monotone_in_window_and_zero_for_no_window():
    rng = random.Random(1)
    assert _ready_prob(rng, 373.0, 428.0, 0.0) == 0.0
    fast_two_second = _ready_prob(random.Random(1), 373.0, 428.0, 2.0)
    fork_two_second = _ready_prob(random.Random(1), 11000.0, 12000.0, 2.0)
    assert fast_two_second > 0.95 and fork_two_second == 0.0
    assert _ready_prob(random.Random(1), 373.0, 428.0, 0.25) < fast_two_second


def test_dataset_join_uses_complete_simulated_profit_only():
    obs = {"events": [{"tx": "0xA", "log_index": 1, "block": 5, "category": "publicly_observable", "lead_seconds_before_winner": None, "collateral_feed": {"proxy_reveal_after_seconds": 0}},
                      {"tx": "0xB", "log_index": 0, "block": 6, "category": "intra_block_only", "lead_seconds_before_winner": None, "collateral_feed": None}]}
    rep = {"items": [{"key": "replay", "detail": {"results": [
        {"tx": "0xa", "log_index": 1, "success": True, "profit_complete": True, "net_profit_usd": "1.5", "historical_effective_gas_price_wei": 7},
        {"tx": "0xb", "log_index": 0, "success": True, "profit_complete": False, "net_profit_usd": "99", "historical_effective_gas_price_wei": 9}]}}]}
    rows = build_dataset(obs, rep)
    assert rows[0].net_profit_usd == Decimal("1.5") and rows[0].winner_gas_price_wei == 7 and rows[0].reveal_after_seconds == 0
    assert rows[1].net_profit_usd is None     # incomplete costs never count as profit


def test_comet_storefront_discount_bonus():
    # storeFrontPriceFactor 60 %, liquidationFactor 95 % -> discount 3 % -> bonus 3/97 = 309 bps
    assert quote_collateral_bonus_bps(6 * 10**17, 95 * FACTOR_SCALE // 100) == 309
    assert quote_collateral_bonus_bps(FACTOR_SCALE, FACTOR_SCALE) == 0
