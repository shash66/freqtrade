from app.strategies.quotes import price_impact_bps


def test_price_impact_vs_oracle_fair_value():
    # 1 WETH ($2000) -> 1,980 USDC quoted: 1% worse than fair -> 100 bps
    assert price_impact_bps(10**18, 18, 2000 * 10**8, 1_980 * 10**6, 6, 1 * 10**8) == 100
    # better than fair value is reported negative
    assert price_impact_bps(10**18, 18, 2000 * 10**8, 2_010 * 10**6, 6, 1 * 10**8) == -50
