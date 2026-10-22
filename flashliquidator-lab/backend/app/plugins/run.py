"""Run one strategy plugin end to end and write its artefact (Phase 5 evaluation driver).

    python -m app.plugins.run --strategy compound_v3 --network base --mode discover|simulate|replay|shadow \\
        [--anvil http://127.0.0.1:8546] [--history docs/phase5-comet-history-base.json] [--from N --to M] [--minutes 30] --out docs/phase5-<strategy>-<network>.json

discover: list opportunities at the head (or --block) and the competition estimate for each (no fork).
simulate: discover, then run the complete transaction on the fork for every executable opportunity.
replay:   the strategy's historical evaluation over [--from, --to] (needs --history for event-based strategies).
shadow:   poll the head for --minutes, record every observation (read-only), summarise frequency and persistence.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from decimal import Decimal

from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.plugins.base import Context
from app.plugins.registry import config_for, load_all


def build_context(slug: str, anvil: str | None, block, extra: dict) -> Context:
    s = get_settings()
    net = build_networks(s)[slug]
    live = RpcClient(f"{slug}-plugin", net.rpc_http or net.archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries, min_interval_ms=0)
    arch = RpcClient(f"{slug}-plugin-archive", net.archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries) if net.archive_rpc else None
    return Context(net, live if block == "latest" else (arch or live), arch, anvil, block, None)


def _net_pre_gas(row: dict) -> str | None:
    """Best quoted spread net of the flash premium: the plugin's own figure, else gross minus the premium it recorded."""
    v = (row.get("state", {}) or {}).get("net_pre_gas_usd")
    if v is not None:
        return str(v)
    prem = (row.get("costs") or {}).get("flash_premium_usd")
    if row.get("gross_profit_usd") is None or prem is None:
        return None
    return str(Decimal(str(row["gross_profit_usd"])) - Decimal(str(prem)))


def summarise_shadow(obs: list[dict], minutes: float) -> dict:
    """Frequency and persistence from shadow ticks. Only *executable* opportunities count (a quoted-but-negative
    spread is an observation, not an opportunity); the per-tick best gross/net series is kept for every tick."""
    keys: dict[str, list[int]] = {}
    series = []
    ticks_with = 0
    for ob in obs:
        rows = ob["opportunities"]
        ex = [x for x in rows if x.get("state", {}).get("executable", True)]
        ticks_with += 1 if ex else 0
        for x in ex:
            keys.setdefault(x["key"], []).append(ob["block"])
        best = max((x for x in rows if x.get("gross_profit_usd") is not None), key=lambda x: float(x["gross_profit_usd"]), default=None)
        series.append({"block": ob["block"], "t": ob["observed_at"], "latency_ms": ob["latency_ms"], "rows": len(rows), "executable": len(ex),
                       "best_gross_usd": best["gross_profit_usd"] if best else None,
                       "best_net_pre_gas_usd": _net_pre_gas(best) if best else None, "best_key": best["key"] if best else None})
    pers = {k: (max(v) - min(v) + 1) for k, v in keys.items()}
    return {"minutes": minutes, "ticks": len(obs), "ticks_with_executable": ticks_with, "distinct_opportunities": len(keys),
            "persistence_blocks": pers, "series": series, "observations": obs[-200:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--network", default="base")
    ap.add_argument("--mode", default="discover", choices=["discover", "simulate", "replay", "shadow"])
    ap.add_argument("--anvil", default=None)
    ap.add_argument("--block", default="latest")
    ap.add_argument("--history", default=None)
    ap.add_argument("--from", dest="from_block", type=int, default=None)
    ap.add_argument("--to", dest="to_block", type=int, default=None)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--max-replays", type=int, default=20)
    ap.add_argument("--force-sim", type=int, default=0, help="simulate: also run the N best-gross non-executable rows as cost-validation runs (expected to revert)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    strategies = load_all()
    strat = strategies[a.strategy]
    extra = {"max_replays": str(a.max_replays)}
    if a.history:
        with open(a.history) as f:
            extra["history"] = json.load(f)
    block = "latest" if a.block == "latest" else int(a.block)
    ctx = build_context(a.network, a.anvil, block, extra)
    ctx.config = config_for(a.strategy, getattr(strat, "cfg", None).__class__() if getattr(strat, "cfg", None) else None)
    ctx.config.extra.update(extra)
    out = {"strategy": a.strategy, "network": a.network, "mode": a.mode, "generated_at": datetime.now(UTC).isoformat(), "block": block}
    if a.mode in ("discover", "simulate"):
        t = time.monotonic()
        opps = strat.discover(ctx)
        out["discovery_ms"] = (time.monotonic() - t) * 1000
        rows = []
        simulable = [o for o in opps if o.state.get("kind") in ("absorb_and_buy", "reserve_purchase", "arbitrage")]
        forced = set()
        if a.mode == "simulate" and a.force_sim:
            ranked = sorted((o for o in simulable if not o.state.get("executable", True)), key=lambda o: -(o.gross_profit_usd or 0))
            forced = {o.key for o in ranked[: a.force_sim]}
        for o in opps:
            row = {"opportunity": o.as_dict()}
            sim = None
            if a.mode == "simulate" and o in simulable and (o.state.get("executable", True) or o.key in forced):
                t = time.monotonic()
                sim = strat.simulate(ctx, o)
                row["simulation"] = sim.as_dict()
                row["simulation_ms"] = (time.monotonic() - t) * 1000
                row["validation_run"] = o.key in forced
            row["competition"] = strat.competition(ctx, o, sim).as_dict()
            rows.append(row)
        out["rows"] = rows
        out["summary"] = {"opportunities": len(opps), "executable": sum(1 for o in opps if o.state.get("kind") not in (None, "scan") and o.state.get("executable", True)),
                          "simulated_success": sum(1 for r in rows if (r.get("simulation") or {}).get("success")),
                          "net_profitable_complete": sum(1 for r in rows if (r.get("simulation") or {}).get("net_profit_complete") and float((r.get("simulation") or {}).get("net_profit_usd") or 0) > 0)}
    elif a.mode == "replay":
        rep = strat.replay(ctx, a.from_block, a.to_block)
        out["replay"] = rep.as_dict()
    else:
        obs = []
        t_end = time.monotonic() + a.minutes * 60
        last_block = None
        while time.monotonic() < t_end:
            head = int(ctx.client.call(lambda: ctx.client.w3.eth.block_number))
            if head != last_block:
                o = strat.shadow_tick(ctx)
                obs.append({"block": o.block, "observed_at": o.observed_at, "latency_ms": round(o.latency_ms, 1),
                            "opportunities": [x for x in o.opportunities if x.get("state", {}).get("kind") not in (None, "scan")], "notes": o.notes,
                            "scan": [x["state"] for x in o.opportunities if x.get("state", {}).get("kind") == "scan"][:1]})
                last_block = head
            time.sleep(a.poll)
        out["shadow"] = summarise_shadow(obs, a.minutes)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)
    print(json.dumps(out.get("summary") or out.get("shadow", {}).get("distinct_opportunities") or (out.get("replay") or {}).get("sample_note") or out, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
