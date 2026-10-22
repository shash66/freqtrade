from decimal import Decimal

from app.chains.config import build_networks
from app.core.config import get_settings
from app.core.money import WAD
from app.indexer.reconcile import priority_score
from app.simulation.replay import ReplayRequest, run_replay


def test_priority_prefers_lower_hf_then_larger_debt():
    big_close = priority_score(int(Decimal("1.01") * WAD), 50_000 * 10**8, 10**8)
    small_close = priority_score(int(Decimal("1.01") * WAD), 500 * 10**8, 10**8)
    far = priority_score(int(Decimal("1.50") * WAD), 50_000 * 10**8, 10**8)
    eligible = priority_score(int(Decimal("0.98") * WAD), 500 * 10**8, 10**8)
    assert eligible > big_close > small_close > far
    assert priority_score(2**256 - 1, 0, 10**8) == 0  # no debt


def test_replay_without_rpc_is_skipped_not_faked(monkeypatch):
    for var in ("BASE_RPC_HTTP", "BASE_ARCHIVE_RPC", "BASE_RPC_WS"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    net = build_networks(get_settings())["base"]
    assert not net.archive_rpc and not net.rpc_http
    r = run_replay(ReplayRequest(network=net, borrower="0x" + "1" * 40, collateral_asset="0x" + "2" * 40, debt_asset="0x" + "3" * 40,
                                 debt_to_cover_raw=10**6, block_number=12_345))
    assert r.success is False and "no archive RPC" in r.revert_reason
    assert r.net_profit_usd is None and r.block_boundary_approximation is True
    assert "simulation skipped" in r.warnings[0]
