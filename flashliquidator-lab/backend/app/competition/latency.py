"""Component latency benchmark (Phase 4, priority 2). Each stage is timed on its own with a monotonic clock:

- rpc_rtt: one trivial eth_call (chain id) round trip to the live endpoint;
- head_poll: eth_blockNumber;
- rpc_reads: the fast path's single Multicall3 read of every protocol input for one position;
- math: the pure profitability functions on those inputs;
- quote: the fast path's quote + L1-fee round trip;
- fast_total: the whole fast path (reads + math + quote + gas price);
- evm_simulation: the full path (fork reset at head + receiver deploy + liquidate) on a private Anvil, optional.

Positions are the lowest-health-factor borrowers known to the database (they need not be liquidatable; the
stages run the same code either way, and the result says whether they were). Output is JSON with per-stage
samples and summary statistics.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.chains.config import NetworkConfig, build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database import models as m
from app.database.base import get_sessionmaker
from app.simulation.fast_path import FastEvaluator


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    ys = sorted(xs)
    return {"n": len(xs), "median": ys[len(ys) // 2], "p90": ys[min(len(ys) - 1, int(round(0.9 * (len(ys) - 1))))], "max": ys[-1], "mean": statistics.fmean(xs)}


def _positions(net: NetworkConfig, n: int) -> list[tuple[str, str, str]]:
    """Lowest-HF borrowers with a known debt and collateral reserve, from the last reconcile."""
    with get_sessionmaker()() as db:
        rows = db.execute(select(m.Borrower).where(m.Borrower.chain_id == net.chain_id, m.Borrower.total_debt_base > 0)
                          .order_by(m.Borrower.health_factor_wad.asc()).limit(n * 4)).scalars().all()
        out = []
        for b in rows:
            states = db.execute(select(m.BorrowerReserveState, m.Asset).join(m.Asset, m.Asset.id == m.BorrowerReserveState.asset_id)
                                .where(m.BorrowerReserveState.borrower_id == b.id)).all()
            debts = [(r, a) for r, a in states if (r.variable_debt + r.stable_debt) > 0]
            colls = [(r, a) for r, a in states if r.a_token_balance > 0 and r.usage_as_collateral]
            if debts and colls:
                debts.sort(key=lambda t: -(t[0].variable_debt + t[0].stable_debt))
                colls.sort(key=lambda t: -t[0].a_token_balance)
                out.append((b.address, colls[0][1].address, debts[0][1].address))
            if len(out) >= n:
                break
    return out


def run(net: NetworkConfig, *, rpc_url: str, positions: int, rounds: int, anvil_url: str | None, evm_rounds: int) -> dict:
    s = get_settings()
    client = RpcClient(f"{net.slug}-latency", rpc_url, s.rpc_timeout_seconds, 1, min_interval_ms=0)
    fe = FastEvaluator(net, client)
    pos = _positions(net, positions)
    samples = {"rpc_rtt_ms": [], "head_poll_ms": [], "rpc_reads_ms": [], "math_ms": [], "quote_ms": [], "fee_estimate_ms": [], "fast_total_ms": [],
               "evm_simulation_ms": []}
    evals = []
    for _ in range(rounds):
        t = time.monotonic()
        client.call(lambda: client.w3.eth.chain_id)
        samples["rpc_rtt_ms"].append((time.monotonic() - t) * 1000)
        t = time.monotonic()
        head = int(client.call(lambda: client.w3.eth.block_number))
        samples["head_poll_ms"].append((time.monotonic() - t) * 1000)
        for borrower, coll, dbt in pos:
            try:
                r = fe.evaluate(borrower, coll, dbt, block=head)
            except Exception as e:  # noqa: BLE001 - recorded, never hidden
                evals.append({"borrower": borrower, "block": head, "error": f"{type(e).__name__}: {str(e)[:120]}"})
                continue
            tm = r.timings
            samples["rpc_reads_ms"].append(tm.rpc_reads_ms)
            samples["math_ms"].append(tm.math_ms)
            if r.eligible:
                samples["quote_ms"].append(tm.quote_ms)
                samples["fee_estimate_ms"].append(tm.fee_estimate_ms)
            else:  # time the quote round trip anyway (nominal amount) so the stage is always measured
                try:
                    t = time.monotonic()
                    fe.quote_round_trip(coll, dbt, 10**15, head)
                    samples["quote_ms"].append((time.monotonic() - t) * 1000)
                except Exception as e:  # noqa: BLE001
                    evals.append({"borrower": borrower, "block": head, "quote_probe_error": str(e)[:100]})
            samples["fast_total_ms"].append(tm.total_ms)
            evals.append({"borrower": borrower, "block": head, "eligible": r.eligible, "health_factor": r.health_factor, "net_profit_usd": r.net_profit_usd,
                          "complete": r.complete, "round_trips": tm.rpc_round_trips, "total_ms": round(tm.total_ms, 1)})
    evm = []
    if anvil_url and pos:
        import os
        os.environ["ANVIL_RPC_URL"] = anvil_url
        get_settings.cache_clear()
        from app.simulation.replay import ReplayRequest, run_replay
        borrower, coll, dbt = pos[0]
        for _ in range(evm_rounds):
            t = time.monotonic()
            rr = run_replay(ReplayRequest(network=net, borrower=borrower, collateral_asset=coll, debt_asset=dbt, debt_to_cover_raw=1, block_number=None))
            dt = (time.monotonic() - t) * 1000
            samples["evm_simulation_ms"].append(dt)
            evm.append({"ms": round(dt, 1), "success": rr.success, "revert_reason": (rr.revert_reason or "")[:80], "execution_path": rr.execution_path})
    out = {"network": net.slug, "generated_at": datetime.now(UTC).isoformat(), "rpc": rpc_url.split("//")[-1].split("/")[0], "anvil": anvil_url,
           "positions": len(pos), "rounds": rounds, "stages": {k: _stats(v) for k, v in samples.items()}, "evaluations": evals, "evm": evm,
           "notes": ["fast path = 2 RPC round trips (Multicall3 reads; Multicall3 quotes + L1 fee) + gas price; nothing is executed",
                     "evm simulation = full path on a private Anvil forked from the archive endpoint; the call reverts when the position is not liquidatable, the time is what is measured",
                     "quote and fee stages are only sampled when the position is liquidatable (otherwise the fast path stops after the math)"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="base")
    ap.add_argument("--rpc", default=None, help="endpoint for the fast path (default: the network's live RPC)")
    ap.add_argument("--positions", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--anvil", default=None)
    ap.add_argument("--evm-rounds", type=int, default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    net = build_networks(s)[a.network]
    out = run(net, rpc_url=a.rpc or net.rpc_http or net.archive_rpc, positions=a.positions, rounds=a.rounds, anvil_url=a.anvil, evm_rounds=a.evm_rounds)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)
    print(json.dumps(out["stages"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
