"""Execution-bid and win-probability model (research only).

Two kinds of numbers are kept apart in every output:

- observed: taken from on-chain liquidations recorded by the indexer (gas price paid by the winner, gas used,
  liquidator concentration) and from the live benchmark (latencies, blocks between our detection and the
  competitor's transaction);
- modeled: probabilities and expected values computed from assumptions stated next to them.

Inclusion models:
- fcfs: the sequencer includes the first valid transaction it receives (OP-stack default); the bid only has
  to clear the base fee; winning depends on being ready before the competitors.
- priority_auction: within one block window the highest priority fee wins (what Base's block builder does
  among transactions that arrive in the same flashblock); the bid is a price and P(win) is the chance that our
  bid exceeds every competitor's bid, estimated from the empirical distribution of winning bids.

Competition-adjusted expected profit of an opportunity with simulated profit P (USD), our gas cost C(bid) and a
failed-attempt cost F:  E = p_win * (P - C(bid)) - (1 - p_win) * F.  For positions that only became liquidatable
inside the winning block (intra-block trigger), a boundary-polling strategy has p_win = 0 by construction and the
value is reported under a separate "requires intra-block reaction" bucket, never added to the capturable total.
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from sqlalchemy import select

from app.chains.config import build_networks
from app.core.config import get_settings
from app.core.money import quantize_usd
from app.database import models as m
from app.database.base import get_sessionmaker

RECEIVER_GAS_UNITS_DEFAULT = 700_000     # measured ~685k on the replayed receiver path (Base); overridden by replay data when present


def empirical_cdf(sample: list[float], x: float) -> float:
    """P(X <= x) for the empirical distribution of `sample` (strictly less-than for ties counts as a loss)."""
    if not sample:
        return 0.0
    return sum(1 for v in sample if v < x) / len(sample)


def p_win_priority_auction(our_bid_wei: float, winning_bids_wei: list[float], n_ready: int) -> float:
    """P(our bid beats every one of `n_ready` competitors whose bids are drawn i.i.d. from the observed winning bids).
    Using *winning* bids as the competitor distribution is conservative: losers bid less than winners."""
    if n_ready <= 0:
        return 1.0
    return empirical_cdf(winning_bids_wei, our_bid_wei) ** n_ready


def p_win_fcfs(our_ready_s: float, competitor_ready_s: list[float]) -> float:
    """Share of observed competitor reaction times that are slower than ours."""
    if not competitor_ready_s:
        return 0.0
    return sum(1 for t in competitor_ready_s if t > our_ready_s) / len(competitor_ready_s)


def n_ready_from_concentration(shares: list[float], horizon_blocks: int = 1) -> float:
    """Expected number of distinct competitors ready within the window, from the observed liquidator shares.
    A liquidator with share s of past wins is assumed active on every opportunity with probability min(1, s*k)
    where k scales with the window; with k=1 this is simply the number of liquidators weighted by share -> 1,
    so we report the count of liquidators with share >= 5% as the effective competitor count (assumption)."""
    return float(sum(1 for s in shares if s >= 0.05)) * max(1, horizon_blocks)


@dataclass
class BidPoint:
    label: str
    bid_wei: int
    bid_gwei: float
    gas_cost_usd: str
    p_win_priority_auction: float
    expected_profit_usd_priority_auction: str


@dataclass
class OpportunityValuation:
    tx_hash: str
    block_number: int
    simulated_profit_usd: str | None
    profit_complete: bool | None
    eligible_at_fork: bool | None
    eligible_at_execution: bool | None
    bucket: str                     # boundary_observable | intra_block_only | not_simulated_profitably
    historical_gas_price_wei: int | None
    historical_gas_used: int | None
    p_win_fcfs: float | None
    p_win_priority_auction_at_median_bid: float | None
    competition_adjusted_usd_fcfs: str | None
    competition_adjusted_usd_priority_auction: str | None


@dataclass
class BidModelReport:
    network: str
    observed: dict
    modeled: dict
    bid_curve: list[BidPoint]
    opportunities: list[OpportunityValuation]
    totals: dict
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _usd(wei: int | float, eth_usd: Decimal) -> Decimal:
    return quantize_usd(Decimal(int(wei)) / Decimal(10**18) * eth_usd)


def build_report(network: str, replays: dict | None, benchmark: dict | None, eth_usd: Decimal, our_ready_seconds: float,
                 failed_attempt_gas_share: float = 0.35, extra_competitors: int = 0) -> BidModelReport:
    s = get_settings()
    net = build_networks(s)[network]
    warnings: list[str] = []
    with get_sessionmaker()() as db:
        rows = db.scalars(select(m.HistoricalLiquidation).join(m.ProtocolDeployment, m.ProtocolDeployment.id == m.HistoricalLiquidation.deployment_id)
                          .where(m.HistoricalLiquidation.chain_id == net.chain_id, m.ProtocolDeployment.pool_address.ilike(net.market.pool),
                                 (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0))).all()
    # one observation per transaction (several events in one tx share the bid)
    by_tx: dict[str, m.HistoricalLiquidation] = {}
    for r in rows:
        by_tx.setdefault(r.tx_hash, r)
    bids = [int(r.effective_gas_price_wei) for r in by_tx.values() if r.effective_gas_price_wei]
    gas_used = [int(r.gas_used) for r in by_tx.values() if r.gas_used]
    l1 = [int(r.l1_fee_wei) for r in by_tx.values() if r.l1_fee_wei]
    liq_counts: dict[str, int] = {}
    for r in by_tx.values():
        liq_counts[r.liquidator] = liq_counts.get(r.liquidator, 0) + 1
    n_tx = len(by_tx)
    shares = sorted((c / n_tx for c in liq_counts.values()), reverse=True) if n_tx else []
    bench_summary = (benchmark or {}).get("summary", {})
    comp_txs = (benchmark or {}).get("competitor_txs", [])
    observed = {
        "liquidation_txs": n_tx, "liquidation_events": len(rows), "unique_liquidators": len(liq_counts),
        "liquidator_shares_top5": [round(x, 3) for x in shares[:5]],
        "winning_effective_gas_price_wei": {"n": len(bids), "p25": _q(bids, .25), "median": _q(bids, .5), "p75": _q(bids, .75), "p90": _q(bids, .9), "max": max(bids) if bids else None},
        "winning_gas_used": {"n": len(gas_used), "median": _q(gas_used, .5), "p90": _q(gas_used, .9)},
        "winning_l1_fee_wei": {"n": len(l1), "median": _q(l1, .5)},
        "benchmark": {k: bench_summary.get(k) for k in ("competitor_txs", "competitor_txs_same_block_as_our_detection", "competitor_txs_before_our_detection",
                                                        "candidates_total", "liquidated_by_competitor", "blocks_alive_when_taken",
                                                        "median_competitor_priority_fee_wei", "median_competitor_effective_gas_price_wei")} if benchmark else None,
        "benchmark_detection_latency_seconds": (benchmark or {}).get("detection_latency_seconds"),
        "benchmark_simulation_latency_seconds": (benchmark or {}).get("simulation_latency_seconds"),
        "benchmark_block_lag_seconds": (benchmark or {}).get("block_lag_wallclock_seconds"),
    }
    if not bids:
        warnings.append("no observed winning gas prices: the priority-auction model cannot be estimated")
    # --- modeled -------------------------------------------------------------------------------------
    n_ready = n_ready_from_concentration(shares) + extra_competitors
    our_gas_units = int(_q([int(x["gas_used"]) for x in (replays or {}).get("results", []) if x.get("success") and x.get("gas_used")], .5) or RECEIVER_GAS_UNITS_DEFAULT)
    l1_med = int(_q(l1, .5) or 0)
    # competitor reaction times: blocks between our detection and their tx, from the benchmark (2 s blocks on Base)
    block_s = 2.0 if net.chain_id == 8453 else 0.25
    comp_react_s = [x["blocks_after_our_detection"] * block_s for x in comp_txs if x.get("blocks_after_our_detection") is not None]
    curve: list[BidPoint] = []
    grid = {"p25": _q(bids, .25), "median": _q(bids, .5), "p75": _q(bids, .75), "p90": _q(bids, .9), "max": max(bids) if bids else None,
            "2x_max": 2 * max(bids) if bids else None}
    median_profit = _median_profit(replays)
    for label, b in grid.items():
        if b is None:
            continue
        cost = _usd(our_gas_units * b + l1_med, eth_usd)
        pw = p_win_priority_auction(b, bids, int(round(n_ready)))
        ep = Decimal(pw) * (median_profit - cost) - Decimal(1 - pw) * cost * Decimal(failed_attempt_gas_share) if median_profit is not None else None
        curve.append(BidPoint(label, int(b), b / 1e9, str(cost), round(pw, 4), str(quantize_usd(ep)) if ep is not None else "n/a"))
    pw_fcfs = p_win_fcfs(our_ready_seconds, comp_react_s) if comp_react_s else None
    modeled = {
        "effective_competitors_ready": n_ready,
        "our_gas_units": our_gas_units, "our_ready_seconds_assumed": our_ready_seconds,
        "p_win_fcfs_vs_observed_competitor_reaction": pw_fcfs,
        "competitor_reaction_samples": len(comp_react_s),
        "median_simulated_profit_usd": str(median_profit) if median_profit is not None else None,
        "failed_attempt_cost_share_of_gas": failed_attempt_gas_share,
        "eth_usd": str(eth_usd),
    }
    # --- per-opportunity valuation -------------------------------------------------------------------
    opps: list[OpportunityValuation] = []
    tot = {"boundary_observable_simulated_profit_usd": Decimal(0), "intra_block_only_simulated_profit_usd": Decimal(0),
           "competition_adjusted_fcfs_usd": Decimal(0), "competition_adjusted_priority_auction_usd": Decimal(0),
           "events_boundary_observable": 0, "events_intra_block_only": 0, "events_not_profitable_or_incomplete": 0}
    med_bid = _q(bids, .5)
    for x in (replays or {}).get("results", []):
        profit = Decimal(x["net_profit_usd"]) if x.get("net_profit_usd") is not None else None
        complete = x.get("profit_complete")
        hist_gp = x.get("historical_effective_gas_price_wei") or None
        if not (x.get("success") and complete and profit is not None and profit > 0):
            bucket = "not_simulated_profitably"
            tot["events_not_profitable_or_incomplete"] += 1
            opps.append(OpportunityValuation(x["tx"], x["block"], str(profit) if profit is not None else None, complete, x.get("eligible_at_fork"),
                                             x.get("eligible_at_execution"), bucket, hist_gp, x.get("historical_gas_used"), None, None, None, None))
            continue
        if x.get("eligible_at_fork") is False:
            bucket = "intra_block_only"
            tot["events_intra_block_only"] += 1
            tot["intra_block_only_simulated_profit_usd"] += profit
            opps.append(OpportunityValuation(x["tx"], x["block"], str(profit), complete, x.get("eligible_at_fork"), x.get("eligible_at_execution"),
                                             bucket, hist_gp, x.get("historical_gas_used"), 0.0, 0.0, "0", "0"))
            continue
        bucket = "boundary_observable"
        tot["events_boundary_observable"] += 1
        tot["boundary_observable_simulated_profit_usd"] += profit
        cost_med = _usd(our_gas_units * (med_bid or 0) + l1_med, eth_usd)
        pw_pa = p_win_priority_auction(med_bid or 0, bids, int(round(n_ready))) if bids else None
        ea_f = quantize_usd(Decimal(pw_fcfs) * (profit - cost_med) - Decimal(1 - pw_fcfs) * cost_med * Decimal(failed_attempt_gas_share)) if pw_fcfs is not None else None
        ea_p = quantize_usd(Decimal(pw_pa) * (profit - cost_med) - Decimal(1 - pw_pa) * cost_med * Decimal(failed_attempt_gas_share)) if pw_pa is not None else None
        if ea_f is not None:
            tot["competition_adjusted_fcfs_usd"] += ea_f
        if ea_p is not None:
            tot["competition_adjusted_priority_auction_usd"] += ea_p
        opps.append(OpportunityValuation(x["tx"], x["block"], str(profit), complete, x.get("eligible_at_fork"), x.get("eligible_at_execution"), bucket,
                                         hist_gp, x.get("historical_gas_used"), pw_fcfs, pw_pa, str(ea_f) if ea_f is not None else None,
                                         str(ea_p) if ea_p is not None else None))
    totals = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in tot.items()}
    if pw_fcfs is None:
        totals["competition_adjusted_fcfs_usd"] = None
        warnings.append("no competitor reaction samples from the benchmark window: the FCFS win probability is not estimated (reported as null, not 0)")
    rep = BidModelReport(network, observed, modeled, curve, opps, totals, assumptions=[
        "competitor bids ~ empirical distribution of *winning* effective gas prices on this Pool (conservative: losers bid less)",
        "effective competitors ready per opportunity = number of liquidators with >= 5% share of observed wins (+ extra_competitors)",
        "our gas = median measured receiver gas on successful replays; L1 fee = median verified L1 fee of winners",
        "failed attempt (lost race, reverted) costs failed_attempt_cost_share_of_gas of a full attempt's gas (revert happens early in the call)",
        "FCFS win probability = share of observed competitor reactions (blocks after our detection) slower than our assumed ready time; null when no reactions were observed",
        "positions only liquidatable inside the winning block (eligible_at_fork=false) are not capturable by boundary polling; their profit is reported separately and excluded from capturable totals",
        "profit per opportunity is the simulated receiver profit (complete costs only); theoretical profit is reported elsewhere",
    ], warnings=warnings)
    return rep


def _q(xs: list, q: float):
    if not xs:
        return None
    ys = sorted(xs)
    return ys[min(len(ys) - 1, int(round(q * (len(ys) - 1))))]


def _median_profit(replays: dict | None) -> Decimal | None:
    ps = [Decimal(x["net_profit_usd"]) for x in (replays or {}).get("results", []) if x.get("success") and x.get("profit_complete") and x.get("net_profit_usd") is not None and Decimal(x["net_profit_usd"]) > 0]
    return quantize_usd(Decimal(statistics.median(ps))) if ps else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Execution bid / win-probability model from observed liquidations and replays")
    ap.add_argument("--network", default="base")
    ap.add_argument("--replays-json", default=None, help="phase3 replay JSON (validation runner output)")
    ap.add_argument("--benchmark-json", default=None)
    ap.add_argument("--eth-usd", default="4000")
    ap.add_argument("--our-ready-seconds", type=float, default=None, help="default: benchmark detection p90 + simulation median, else 2.0")
    ap.add_argument("--extra-competitors", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    replays = benchmark = None
    if a.replays_json:
        with open(a.replays_json) as f:
            doc = json.load(f)
        replays = next((i["detail"] for i in doc.get("items", []) if i.get("key") == "replay"), doc)
    if a.benchmark_json:
        with open(a.benchmark_json) as f:
            benchmark = json.load(f)
    ready = a.our_ready_seconds
    if ready is None:
        d = ((benchmark or {}).get("detection_latency_seconds") or {}).get("p90")
        sm = ((benchmark or {}).get("simulation_latency_seconds") or {}).get("median")
        ready = (d or 0.5) + (sm or 1.5) if benchmark else 2.0
    rep = build_report(a.network, replays, benchmark, Decimal(a.eth_usd), ready, extra_competitors=a.extra_competitors)
    out = json.dumps(rep.as_dict(), indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(out)
    print(json.dumps({"observed": rep.observed, "modeled": rep.modeled, "totals": rep.totals, "bid_curve": [asdict(b) for b in rep.bid_curve],
                      "warnings": rep.warnings}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
