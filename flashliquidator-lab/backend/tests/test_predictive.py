from decimal import Decimal

from app.strategies.predictive import ReserveLeg, health_factor, scenarios, trigger_prices

WETH = ReserveLeg("WETH", "0xweth", 18, 2_000 * 10**8, 6 * 10**18, 8300, True)
CBBTC = ReserveLeg("cbBTC", "0xbtc", 8, 60_000 * 10**8, 10**7, 7500, True)  # 0.1 BTC
USDC = ReserveLeg("USDC", "0xusdc", 6, 1 * 10**8, 10_000 * 10**6, 0, False)


def test_health_factor_matches_hand_calculation():
    # (12000*0.83 + 6000*0.75) / 10000 = (9960 + 4500) / 10000 = 1.446
    assert health_factor([WETH, CBBTC], [USDC]) == Decimal("1.446")


def test_trigger_price_single_collateral():
    t = trigger_prices([WETH], [USDC])[0]
    assert t["trigger_price_usd"] == "2008.03212851" and t["drop_to_trigger_pct"] == "-0.40"


def test_trigger_price_multi_collateral_holds_others_fixed():
    t = {x["collateral"]: x for x in trigger_prices([WETH, CBBTC], [USDC])}
    # WETH*: (10000 - 4500) / (6 * 0.83) = 1104.42
    assert t["WETH"]["trigger_price_usd"] == "1104.41767068"
    # cbBTC*: (10000 - 9960) / (0.1 * 0.75) = 533.33
    assert t["cbBTC"]["trigger_price_usd"] == "533.33333333"


def test_scenarios_shapes_and_accrual():
    s = scenarios([WETH, CBBTC], [USDC], annual_borrow_rate=Decimal("0.10"), horizon_days=36)
    assert Decimal(s["health_factor_now"]) == Decimal("1.446")
    corr = {r["shock_pct"]: Decimal(r["health_factor"]) for r in s["correlated_shocks"]}
    assert corr[-10] < corr[-5] < corr[-1] < Decimal("1.446")
    assert Decimal(s["interest_accrual"]["debt_multiplier"]) == Decimal("1.009863")
    assert Decimal(s["interest_accrual"]["health_factor"]) < Decimal("1.446")
    assert "prediction" in s["note"]
