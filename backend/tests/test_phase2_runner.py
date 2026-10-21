"""The runner must report BLOCKED with reasons when the chain is unreachable, never invented results."""
from app.core.config import get_settings
from app.validation import phase2


def test_runner_reports_blocked_without_rpc():
    get_settings.cache_clear()
    rep = phase2.run("base", blocks=10, replays=1)
    by = {i.key: i for i in rep.items}
    assert by["readonly"].status == "EXECUTED" and by["readonly"].detail["live_trading_enabled"] is False
    assert by["rpc"].status == "BLOCKED" and "no RPC configured" in by["rpc"].reason
    for k in ("index", "reconcile", "replay", "triage", "competitors"):
        assert by[k].status == "BLOCKED"
    md = rep.to_markdown()
    assert "**EXECUTED**" in md and "**BLOCKED**" in md and "net_profit" not in md
