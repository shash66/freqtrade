"""Strategy comparison on one historical dataset (Phase 4, priority 8).

Dataset: the recorded liquidation events of a network with, per event, the Phase 4 observability category and
lead, the Phase 3 full-path simulated net profit (complete costs only), the proxy reveal delay, and the
winner's effective gas price. Every strategy is evaluated on exactly the same events with the same seed.

Strategies (what each can act on, and when it is ready):

- A `boundary_polling`: acts only on publicly_observable events (liquidatable at N−1 with the Pool's own prices);
  ready = detection latency + fast-path evaluation, then the fork simulation gate (full path) before sending.
- B `event_driven`: same event set, but no fork gate: ready = detection latency + fast path.
- C `predictive`: also acts on predictable_not_yet_eligible events (the aggregator already carried the round
  or interest alone makes the position liquidatable at block N), with the transaction prepared in advance and
  submitted at the Pool's reveal block; capture requires the event to still be open then (measured share).
- D `intra_block` (reference only): would need to act inside the winning block (SVR-style auction
  participation or same-block backruns); not a public-data strategy, so its capture is reported as 0 and its
  profit as the ceiling it would need.

Win model per event: priority-auction P(win) from the empirical winning-bid distribution of this Pool at the
strategy's bid (median winning bid × multiplier), compounded over the effective competitor count, times a
readiness factor: P(ready before the window closes) from a seeded latency draw against the event's window
(publicly observable: one block; predictable: the lead). Expected profit per event = p × net − (1 − p) × failed
cost. Confidence intervals: seeded bootstrap over events (profit per event) and the Wilson interval for
capture counts. All inputs are echoed.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from app.competition.bidmodel import p_win_priority_auction
from app.competition.montecarlo import _wilson

STRATEGIES = {
    "A_boundary_polling": {"acts_on": ["publicly_observable"], "fork_gate": True, "bid_multiplier": 1.0,
                           "description": "poll every head, evaluate on the fast path, confirm on a fork, then send"},
    "B_event_driven": {"acts_on": ["publicly_observable"], "fork_gate": False, "bid_multiplier": 1.1,
                       "description": "poll every head, evaluate on the fast path, send without a fork gate"},
    "C_predictive": {"acts_on": ["publicly_observable", "predictable_not_yet_eligible"], "fork_gate": False, "bid_multiplier": 1.2,
                     "description": "read the aggregators directly, precompute the transaction during the lead, submit at the reveal block"},
    "D_ceiling_no_competition": {"acts_on": ["publicly_observable", "predictable_not_yet_eligible", "intra_block_only"], "fork_gate": False, "bid_multiplier": 1.0,
                                 "description": "reference ceiling: every simulated-profitable event captured with certainty; not a strategy, the upper bound"},
}


@dataclass
class EventRow:
    tx: str
    log_index: int
    block: int
    category: str
    lead_seconds: float | None
    reveal_after_seconds: float | None
    net_profit_usd: Decimal | None      # simulated, complete costs; None when not simulated profitably/completely
    winner_gas_price_wei: int | None


@dataclass
class StrategyResult:
    name: str
    description: str
    events_in_scope: int
    events_with_profit: int
    expected_profit_total_usd: str
    expected_profit_ci95_usd: tuple[str, str]
    expected_profit_per_event_usd: str
    capture_probability_mean: float
    modeled_captures: float
    captures_ci95_wilson: tuple[float, float]
    ready_in_time_rate: float
    bid_gwei: float
    p_win_bid: float
    per_event: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def build_dataset(observability: dict, replays: dict) -> list[EventRow]:
    rd = next((i["detail"] for i in replays.get("items", []) if i.get("key") == "replay"), {})
    rep = {(r["tx"].lower(), r.get("log_index")): r for r in rd.get("results", [])}
    rows = []
    for e in observability.get("events", []):
        r = rep.get((e["tx"].lower(), e["log_index"]))
        net = None
        if r and r.get("success") and r.get("profit_complete") and r.get("net_profit_usd") is not None:
            net = Decimal(r["net_profit_usd"])
        reveal = (e.get("collateral_feed") or {}).get("proxy_reveal_after_seconds")
        rows.append(EventRow(e["tx"], e["log_index"], e["block"], e["category"], e.get("lead_seconds_before_winner"), reveal, net,
                             r.get("historical_effective_gas_price_wei") if r else None))
    return rows


def _ready_prob(rng: random.Random, latency_ms_median: float, latency_ms_p90: float, window_s: float, draws: int = 200) -> float:
    """P(latency draw <= window). Log-normal fitted to median/p90; a window of 0 means 'must be ready before the block' -> 0."""
    if window_s <= 0:
        return 0.0
    mu = math.log(max(latency_ms_median, 1e-3))
    sigma = max((math.log(max(latency_ms_p90, latency_ms_median + 1e-3)) - mu) / 1.2816, 1e-6)
    ok = 0
    for _ in range(draws):
        if math.exp(rng.gauss(mu, sigma)) <= window_s * 1000:
            ok += 1
    return ok / draws


def availability(r: EventRow) -> tuple[float, str]:
    """Could a public bot have raced for this event at all?  publicly_observable: yes (race in the next block).
    predictable: yes when the Pool's price was public at the winning block (reveal 0 s) or eligibility came from
    time alone; no when the winner acted before the proxy revealed the round (SVR-style exclusive window, measured
    from the reveal delay). intra-block-only: no."""
    if r.category == "publicly_observable":
        return 1.0, "race in the next block"
    if r.category == "predictable_not_yet_eligible":
        if r.reveal_after_seconds is None or r.reveal_after_seconds == 0:
            return 1.0, "price public at the winning block (reveal 0 s) or time-only eligibility: race at the reveal block"
        return 0.0, f"winner acted {r.reveal_after_seconds} s before the public reveal: exclusive window, not contestable"
    return 0.0, "no public signal before the winning block"


def evaluate(rows: list[EventRow], latency: dict, winning_bids: list[int], competitors: int, failed_cost_usd: Decimal,
             predictable_available_share: float | None, block_seconds: float, seed: int = 42, boot: int = 2000) -> dict:
    rng = random.Random(seed)
    med_bid = sorted(winning_bids)[len(winning_bids) // 2] if winning_bids else None
    pred = [r for r in rows if r.category == "predictable_not_yet_eligible"]
    measured_share = (sum(availability(r)[0] for r in pred) / len(pred)) if pred else None
    st = latency.get("stages", {})
    fast_med, fast_p90 = st.get("fast_total_ms", {}).get("median", 700.0), st.get("fast_total_ms", {}).get("p90", 1000.0)
    evm_med, evm_p90 = st.get("evm_simulation_ms", {}).get("median", 17000.0), st.get("evm_simulation_ms", {}).get("p90", 19000.0)
    head_med, head_p90 = st.get("head_poll_ms", {}).get("median", 300.0), st.get("head_poll_ms", {}).get("p90", 500.0)
    results = []
    bid_grid = sorted({int(b) for b in winning_bids} | {int(med_bid * m) for m in (0.5, 1.0, 1.1, 1.2, 1.5, 2.0, 3.0)} if winning_bids else set())
    for name, cfg in STRATEGIES.items():
        in_scope = [r for r in rows if r.category in cfg["acts_on"]]
        best_bid = None
        if name != "D_ceiling_no_competition" and bid_grid:
            # the bid that maximises expected profit over the in-scope events (gas cost scales with the bid)
            def _ev_at(b, scope=in_scope):
                pw = p_win_priority_auction(b, winning_bids, competitors)
                tot = Decimal(0)
                for r in scope:
                    av, _ = availability(r)
                    if r.net_profit_usd is None or av == 0:
                        continue
                    extra_gas = Decimal(max(b - (med_bid or 0), 0)) * Decimal(565_190) / Decimal(10**18) * Decimal(4000)
                    pc = Decimal(str(pw)) * Decimal(str(av))
                    tot += pc * (r.net_profit_usd - extra_gas) - (Decimal(1) - pc) * failed_cost_usd
                return tot
            best_bid = max(bid_grid, key=_ev_at)
        bid = best_bid if best_bid is not None else (int(med_bid * cfg["bid_multiplier"]) if med_bid else None)
        p_win = p_win_priority_auction(bid, winning_bids, competitors) if bid else 0.0
        per_event = []
        values: list[Decimal] = []
        ready_rates = []
        for r in in_scope:
            lat_med = head_med + fast_med + (evm_med if cfg["fork_gate"] else 0)
            lat_p90 = head_p90 + fast_p90 + (evm_p90 if cfg["fork_gate"] else 0)
            if r.category == "publicly_observable":
                window = block_seconds            # must be in the next block
            elif r.category == "predictable_not_yet_eligible":
                window = max(float(r.lead_seconds or 0), block_seconds)
            else:
                window = 0.0
            ready = _ready_prob(rng, lat_med, lat_p90, window)
            avail, _why = availability(r)
            if r.category == "predictable_not_yet_eligible" and predictable_available_share is not None:
                avail = predictable_available_share    # explicit override (labeled in inputs)
            if name == "D_ceiling_no_competition":
                ready, p_win_e, avail = 1.0, 1.0, 1.0
            else:
                p_win_e = p_win
            p = ready * p_win_e * avail
            net = r.net_profit_usd
            pd = Decimal(str(p))
            value = (pd * net - (Decimal(1) - pd) * failed_cost_usd * Decimal(1 if ready > 0 and avail > 0 else 0)) if net is not None else Decimal(0)
            values.append(value)
            ready_rates.append(ready)
            per_event.append({"tx": r.tx, "log_index": r.log_index, "category": r.category, "net_profit_usd": None if net is None else str(net),
                              "window_s": window, "p_ready": round(ready, 3), "p_win_bid": round(p_win_e, 3), "availability": avail, "availability_reason": _why, "p_capture": round(p, 4),
                              "expected_value_usd": str(value.quantize(Decimal("0.0001")))})
        total = sum(values, Decimal(0))
        # bootstrap over events
        boots = []
        if values:
            for _ in range(boot):
                sample = [values[rng.randrange(len(values))] for _ in values]
                boots.append(sum(sample, Decimal(0)))
            boots.sort()
            lo, hi = boots[int(0.025 * boot)], boots[int(0.975 * boot) - 1]
        else:
            lo = hi = Decimal(0)
        caps = sum(float(x["p_capture"]) for x in per_event)
        wil = _wilson(int(round(caps)), len(in_scope)) if in_scope else (0.0, 0.0)
        results.append(StrategyResult(name, cfg["description"], len(in_scope), sum(1 for r in in_scope if r.net_profit_usd is not None),
                                      str(total.quantize(Decimal("0.0001"))), (str(lo.quantize(Decimal("0.0001"))), str(hi.quantize(Decimal("0.0001")))),
                                      str((total / len(in_scope)).quantize(Decimal("0.0001"))) if in_scope else "0",
                                      round(statistics.fmean([x["p_capture"] for x in per_event]), 4) if per_event else 0.0, round(caps, 3), wil,
                                      round(statistics.fmean(ready_rates), 3) if ready_rates else 0.0, (bid or 0) / 1e9, round(p_win, 4), per_event,
                                      [f"latency: head poll + fast path{' + fork gate' if cfg['fork_gate'] else ''} drawn log-normal from measured median/p90",
                                       f"window: one block ({block_seconds} s) for publicly observable events; the measured lead for predictable events",
                                       f"bid chosen from the observed winning-bid grid to maximise expected profit over in-scope events (extra gas above the median bid charged at 565,190 units, ETH 4000 USD); P(win) = F(bid)^{competitors} over observed winning bids",
                                       "profit per captured event = Phase 3 simulated net profit with complete costs; events without one contribute 0",
                                       "predictable events are multiplied by the measured share still open at the reveal block (0 when unmeasured and reported as such)"]))
    return {"seed": seed, "bootstrap_samples": boot, "events": len(rows), "categories": {c: sum(1 for r in rows if r.category == c) for c in ("publicly_observable", "predictable_not_yet_eligible", "intra_block_only", "unknown")},
            "inputs": {"winning_bids_n": len(winning_bids), "median_winning_bid_gwei": (med_bid or 0) / 1e9, "competitors": competitors, "failed_attempt_cost_usd": str(failed_cost_usd),
                       "predictable_available_share_override": predictable_available_share,
                       "predictable_available_share_measured": None if measured_share is None else round(measured_share, 3),
                       "predictable_events": len(pred), "block_seconds": block_seconds, "bid_grid_gwei": [b / 1e9 for b in bid_grid],
                       "latency_ms": {"head_poll": [head_med, head_p90], "fast_path": [fast_med, fast_p90], "evm_simulation": [evm_med, evm_p90]}},
            "strategies": [asdict(r) for r in results],
            "verdict": _verdict(results)}


def _verdict(results: list[StrategyResult]) -> dict:
    out = {}
    for r in results:
        lo, hi = Decimal(r.expected_profit_ci95_usd[0]), Decimal(r.expected_profit_ci95_usd[1])
        total = Decimal(r.expected_profit_total_usd)
        out[r.name] = {"expected_profit_total_usd": str(total), "ci95": [str(lo), str(hi)],
                       "positive_after_costs_and_competition": bool(lo > 0), "point_estimate_positive": bool(total > 0),
                       "statement": ("positive expected profit with 95 % confidence" if lo > 0 else
                                     "point estimate positive but the interval includes zero" if total > 0 else "no positive expected profit on this dataset")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="base")
    ap.add_argument("--observability-json", required=True)
    ap.add_argument("--replays-json", required=True)
    ap.add_argument("--latency-json", required=True)
    ap.add_argument("--bidmodel-json", required=True)
    ap.add_argument("--predictable-available-share", type=float, default=None)
    ap.add_argument("--failed-cost-usd", default="0.20")
    ap.add_argument("--competitors", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    with open(a.observability_json) as f:
        obs = json.load(f)
    with open(a.replays_json) as f:
        rep = json.load(f)
    with open(a.latency_json) as f:
        lat = json.load(f)
    with open(a.bidmodel_json) as f:
        bm = json.load(f)
    rows = build_dataset(obs, rep)
    bids = [int(r.winner_gas_price_wei) for r in rows if r.winner_gas_price_wei]
    # one bid per transaction (several events share a tx)
    seen, uniq = set(), []
    for r in rows:
        if r.winner_gas_price_wei and r.tx not in seen:
            seen.add(r.tx)
            uniq.append(int(r.winner_gas_price_wei))
    comp = a.competitors if a.competitors is not None else int(round(bm["modeled"]["effective_competitors_ready"]))
    block_s = 2.0 if a.network == "base" else 0.25
    out = evaluate(rows, lat, uniq or bids, comp, Decimal(a.failed_cost_usd), a.predictable_available_share, block_s, a.seed)
    out["network"] = a.network
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)
    print(json.dumps({"categories": out["categories"], "verdict": out["verdict"], "inputs": out["inputs"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
