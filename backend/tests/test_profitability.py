"""Worked examples with hand-checked numbers. Prices are 8-decimal oracle values."""
from decimal import Decimal

from app.core.money import WAD
from app.strategies import profitability as pf

USDC = pf.ReserveParams("0xusdc", "USDC", 6, 1 * 10**8, 10_450, 1000, 7800)
WETH = pf.ReserveParams("0xweth", "WETH", 18, 2_000 * 10**8, 10_500, 1000, 8300)  # 5% bonus, 10% protocol fee on bonus


def position(hf=Decimal("0.97"), debt_usdc=10_000, coll_weth=Decimal(6), total_debt_base=None, total_coll_base=None):
    return pf.PositionInputs(
        health_factor_wad=int(hf * WAD), total_collateral_base=total_coll_base or int(coll_weth * 2_000 * 10**8),
        total_debt_base=total_debt_base or debt_usdc * 10**8, debt=USDC, collateral=WETH,
        user_debt_raw=debt_usdc * 10**6, user_collateral_raw=int(coll_weth * 10**18), collateral_enabled=True)


def test_close_factor_50_percent_above_threshold():
    e = pf.check_eligibility(position(hf=Decimal("0.97")), pf.LiquidationRules())
    assert e.eligible and e.close_factor_bps == 5000 and e.max_debt_to_cover_raw == 5_000 * 10**6


def test_close_factor_100_percent_below_095():
    e = pf.check_eligibility(position(hf=Decimal("0.90")), pf.LiquidationRules())
    assert e.close_factor_bps == 10_000 and e.max_debt_to_cover_raw == 10_000 * 10**6


def test_v33_small_position_gets_full_close_factor_but_v32_does_not():
    small = position(hf=Decimal("0.98"), debt_usdc=1_500, coll_weth=Decimal("0.9"))
    assert pf.check_eligibility(small, pf.LiquidationRules()).close_factor_bps == 10_000
    v32 = pf.LiquidationRules(version="3.2", min_leftover_base=0, min_base_max_liquidation_threshold=0)
    assert pf.check_eligibility(small, v32).close_factor_bps == 5_000


def test_not_eligible_when_healthy():
    e = pf.check_eligibility(position(hf=Decimal("1.01")), pf.LiquidationRules())
    assert not e.eligible and "health factor" in e.reasons[0]


def test_collateral_math_matches_aave_formula():
    # repay 5,000 USDC at $1, WETH $2,000, bonus 5% -> 2.5 WETH * 1.05 = 2.625 WETH seized
    p = position()
    out = pf.apply_liquidation(p, pf.LiquidationRules(), 5_000 * 10**6)
    assert out.collateral_seized_raw == 2_625 * 10**15
    # bonus collateral = 2.625 - 2.625/1.05 = 0.125 WETH; protocol fee 10% of that = 0.0125 WETH
    assert out.protocol_fee_raw == 12_500 * 10**12
    assert out.collateral_to_liquidator_raw == out.collateral_seized_raw - out.protocol_fee_raw
    assert not out.capped_by_collateral


def test_collateral_cap_reduces_debt_to_cover():
    p = position(coll_weth=Decimal(1))  # only 1 WETH available
    out = pf.apply_liquidation(p, pf.LiquidationRules(), 5_000 * 10**6)
    assert out.capped_by_collateral and out.collateral_seized_raw == 10**18
    # debt covered = 1 WETH * 2000 / 1.05 = 1904.76 USDC (percentDiv rounding)
    assert abs(out.debt_to_cover_raw - 1_904_761_905) <= 1


def test_dust_rule_forces_full_repayment_when_allowed():
    # HF 0.90 -> 100% close factor allowed; 50% would leave $900 debt (< $1000 min leftover)
    p = position(hf=Decimal("0.90"), debt_usdc=1_800, coll_weth=Decimal(1))
    pf.check_eligibility(p, pf.LiquidationRules())
    rules = pf.LiquidationRules()
    half = pf.apply_liquidation(p, rules, 900 * 10**6)
    assert pf.dust_check(p, rules, half)  # leaves 900 USD debt -> dust
    out, problems = pf.choose_debt_to_cover(p, rules, pf.Eligibility(True, [], 10_000, 900 * 10**6))
    assert not problems and out.debt_to_cover_raw == 1_800 * 10**6


def test_profit_report_complete_case():
    p = position()
    rules = pf.LiquidationRules()
    out = pf.apply_liquidation(p, rules, 5_000 * 10**6)
    # swap 2.6125 WETH -> say 5,200 USDC (after DEX fees/impact); premium 5 bps; gas 300k @ 0.01 gwei, ETH $2,000
    costs = pf.CostInputs(flash_premium_total_bps=5, gas_units=300_000, gas_price_wei=10**7, native_price_raw=2_000 * 10**8,
                          l1_data_fee_wei=0, swap_out_raw=5_200 * 10**6, swap_source="test", swap_impact_bps=40)
    r = pf.profit_report(p, rules, out, costs)
    assert r.complete
    assert r.flash_premium_raw == 2_500_000  # 5000 * 0.0005 = 2.5 USDC
    assert r.gas_cost_usd == Decimal("0.006")  # 3e12 wei = 0.000003 ETH * 2000
    # net = 5200 - 5000 - 2.5 - 0.006 = 197.494 USDC
    assert r.net_profit_usd == Decimal("197.494")
    assert r.breakeven_gas_usd == Decimal("197.5")
    assert r.return_on_notional == Decimal("0.0394988")
    assert r.breakeven_slippage_bps == int(197.494 * 10**6 * 10_000 // (5_200 * 10**6))
    assert r.gross_bonus_usd == Decimal(225)  # 2.6125 WETH * 2000 - 5000
    assert set(r.sensitivity) == {"collateral_-1%", "collateral_-5%", "collateral_+1%"}


def test_profit_report_marks_incomplete_without_quote_or_gas():
    p = position()
    rules = pf.LiquidationRules()
    out = pf.apply_liquidation(p, rules, 5_000 * 10**6)
    r = pf.profit_report(p, rules, out, pf.CostInputs(flash_premium_total_bps=None, gas_units=None, gas_price_wei=None, native_price_raw=None))
    assert not r.complete and r.net_profit_usd is None
    assert any("premium" in x for x in r.incomplete_reasons) and any("swap quote" in x for x in r.incomplete_reasons)
    assert any("gas cost unknown" in x for x in r.incomplete_reasons)


def test_trigger_price_prediction():
    # 6 WETH collateral, LT 83%, $10,000 debt -> price* = 10000 / (6 * 0.83) = 2008.03
    p = position()
    assert pf.trigger_price(p) == Decimal("2008.03212851")
