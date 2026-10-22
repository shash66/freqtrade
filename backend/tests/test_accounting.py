from decimal import Decimal

from app.core.money import quantize_usd
from app.simulation.accounting import reconcile_profit

USDC_PRICE, ETH_PRICE = 1 * 10**8, 2_000 * 10**8


def test_profit_reconciles_from_measured_balances_and_all_costs():
    # swapOut 9.653389, repaid 9.149694, premium 0.004575 -> surplus 0.499120 USDC; gas 569,713 @ 1 gwei + L1 4.1e9 wei
    r = reconcile_profit(swap_out_raw=9_653_389, repaid_raw=9_149_694, premium_raw=4_575, event_surplus_raw=499_120,
                         gas_used=569_713, gas_price_wei=10**9, l1_data_wei=4_107_912_235, l1_source="estimated",
                         native_price_raw=ETH_PRICE, debt_price_raw=USDC_PRICE, debt_decimals=6)
    assert r.complete and r.surplus_matches_event is True and r.surplus_raw == 499_120
    fee_wei = 569_713 * 10**9 + 4_107_912_235
    assert r.total_fee_wei == fee_wei
    assert r.fee_usd == quantize_usd(Decimal(fee_wei) / Decimal(10**18) * 2000)
    assert r.net_profit_usd == quantize_usd(Decimal("0.499120") - r.fee_usd)
    assert r.gross_surplus_usd == Decimal("0.49912")


def test_surplus_mismatch_is_flagged_and_event_wins():
    r = reconcile_profit(swap_out_raw=100, repaid_raw=90, premium_raw=1, event_surplus_raw=8, gas_used=1, gas_price_wei=1,
                         l1_data_wei=0, l1_source="not_applicable", native_price_raw=ETH_PRICE, debt_price_raw=USDC_PRICE, debt_decimals=6)
    assert r.surplus_matches_event is False and r.surplus_raw == 8


def test_unknown_l1_fee_makes_result_incomplete_not_profitable():
    r = reconcile_profit(swap_out_raw=10**7, repaid_raw=9 * 10**6, premium_raw=4_500, event_surplus_raw=None, gas_used=600_000,
                         gas_price_wei=10**7, l1_data_wei=None, l1_source="unknown", native_price_raw=ETH_PRICE, debt_price_raw=USDC_PRICE, debt_decimals=6)
    assert not r.complete and r.net_profit_usd is None and r.total_fee_wei is None
    assert any("L1 data fee" in m for m in r.missing)
    # same inputs on a chain without an L1 component are complete
    r2 = reconcile_profit(swap_out_raw=10**7, repaid_raw=9 * 10**6, premium_raw=4_500, event_surplus_raw=None, gas_used=600_000,
                          gas_price_wei=10**7, l1_data_wei=None, l1_source="not_applicable", native_price_raw=ETH_PRICE,
                          debt_price_raw=USDC_PRICE, debt_decimals=6, l1_required=False)
    assert r2.complete and r2.net_profit_usd is not None


def test_negative_net_is_signed_correctly():
    r = reconcile_profit(swap_out_raw=1_000_000, repaid_raw=999_000, premium_raw=500, event_surplus_raw=500, gas_used=700_000,
                         gas_price_wei=10**9, l1_data_wei=10**12, l1_source="estimated", native_price_raw=ETH_PRICE, debt_price_raw=USDC_PRICE, debt_decimals=6)
    assert r.complete and r.net_profit_usd < 0 and r.net_profit_debt_raw < 0
