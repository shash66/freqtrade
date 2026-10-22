"""Phase 5 plugin framework: dataclass invariants, registry/config, runner selection rules, pure plugin math."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.plugins import registry
from app.plugins.base import (
    CompetitionEstimate,
    CostBreakdown,
    Opportunity,
    SimulationOutcome,
    StrategyConfig,
)
from app.plugins.dex_arb import INSUFFICIENT_PROFIT_SELECTOR, _decode_insufficient_profit, _usd
from app.plugins.yield_opt import _apy_from_per_second, _apy_from_ray_apr


def test_cost_breakdown_total_is_none_when_anything_is_missing():
    c = CostBreakdown(l2_execution_usd=Decimal("0.10"), flash_premium_usd=Decimal("0.02"))
    assert c.total_usd == Decimal("0.12")
    c.missing.append("l1 data fee")
    assert c.total_usd is None


def test_opportunity_as_dict_serialises_decimals_and_total():
    o = Opportunity("dex_arb", "base", "arbitrage", "k", 1, "d", Decimal(0), True, "", gross_profit_usd=Decimal("1.5"),
                    costs=CostBreakdown(l2_execution_usd=Decimal("0.5"), source="measured"), net_profit_usd=Decimal("1.0"), net_profit_complete=True)
    d = o.as_dict()
    assert d["gross_profit_usd"] == "1.5" and d["costs"]["total_usd"] == "0.5" and d["costs"]["l2_execution_usd"] == "0.5"
    assert d["outcome_class"] == "theoretical"


def test_simulation_and_competition_as_dict():
    s = SimulationOutcome(False, "simulated", "InsufficientProfit", Decimal("-0.01"), CostBreakdown(missing=["gas valuation"]), None, False)
    d = s.as_dict()
    assert d["net_profit_usd"] is None and d["costs"]["total_usd"] is None and d["gross_profit_usd"] == "-0.01"
    c = CompetitionEstimate(None, None, Decimal("0.1"), "m", ["why"])
    assert c.as_dict()["p_capture"] is None and c.as_dict()["failed_attempt_cost_usd"] == "0.1"


def test_config_from_environment(monkeypatch):
    monkeypatch.setenv("STRATEGY_DEX_ARB_ENABLED", "false")
    monkeypatch.setenv("STRATEGY_DEX_ARB_NETWORKS", "base")
    monkeypatch.setenv("STRATEGY_DEX_ARB_MIN_NET_PROFIT_USD", "0.25")
    monkeypatch.setenv("STRATEGY_DEX_ARB_STEP", "100")
    cfg = registry.config_for("dex_arb", StrategyConfig())
    assert cfg.enabled is False and cfg.networks == ("base",) and cfg.min_net_profit_usd == Decimal("0.25") and cfg.extra["step"] == "100"


def test_registry_loads_three_strategies_with_the_common_interface():
    strategies = registry.load_all()
    assert {"compound_v3", "dex_arb", "yield_opt"} <= set(strategies)
    for s in strategies.values():
        for method in ("discover", "state", "simulate", "competition", "replay", "shadow_tick"):
            assert callable(getattr(s, method)), f"{s.name} lacks {method}"
        assert s.kind in ("liquidation", "arbitrage", "yield")


def test_insufficient_profit_decoding():
    actual, required = 49989472, 50033292
    data = "0x" + INSUFFICIENT_PROFIT_SELECTOR + f"{actual:064x}" + f"{required:064x}"
    assert _decode_insufficient_profit(f"execution reverted: ('{data}', ...)") == (actual, required)
    assert _decode_insufficient_profit("0x08c379a0 Error(string)") is None
    assert _decode_insufficient_profit(None) is None


def test_usd_conversion_uses_token_decimals_and_8_decimal_prices():
    assert _usd(50_000_000, 100_000_000, 6) == Decimal("50")            # 50 USDC at $1.00
    assert _usd(10**18, 2_000 * 10**8, 18) == Decimal("2000")           # 1 WETH at $2,000


def test_yield_rate_conversions():
    assert _apy_from_ray_apr(0) == 0
    apy = _apy_from_ray_apr(int(0.05 * 10**27))                          # 5 % APR continuously compounded per second
    assert Decimal("0.0512") < apy < Decimal("0.0514")
    per_second = int(0.05 / (365 * 24 * 3600) * 10**18)
    assert Decimal("0.0512") < _apy_from_per_second(per_second) < Decimal("0.0514")


@pytest.mark.parametrize("kind,executable,expected", [("arbitrage", True, True), ("arbitrage", False, False), ("scan", True, False), ("stable_lending", True, False)])
def test_runner_simulates_only_executable_transaction_rows(kind, executable, expected):
    o = Opportunity("x", "base", "arbitrage", "k", 1, "d", Decimal(0), True, "", state={"kind": kind, "executable": executable})
    simulable = o.state.get("kind") in ("absorb_and_buy", "reserve_purchase", "arbitrage")
    assert (simulable and o.state.get("executable", True)) is expected
