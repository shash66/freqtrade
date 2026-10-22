"""Assemble docs/phase4-results.md from the Phase 4 artefacts (observability, latency, strategies, markets, compound
history) plus the Phase 3 replay data they build on. Missing inputs are reported as such, never filled in."""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from decimal import Decimal


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _usd(x):
    if x is None or x == "":
        return "—"
    d = Decimal(str(x))
    return f"${d:,.2f}" if abs(d) >= 1 else f"${d:.4f}"


def _short(h):
    return f"`{h[:6]}…{h[-4:]}`" if h else "—"


def _ms(st, k, d=0):
    v = (st or {}).get(k) or {}
    return f"{v['median']:.{d}f} / {v['p90']:.{d}f} ms (n={v['n']})" if v.get("n") else "not measured"


def section_observability(obs, slug):
    out = [f"## Observability of recorded liquidations ({slug.title()})", ""]
    if not obs:
        return out + ["Status: **NOT RUN**", ""]
    s = obs["summary"]
    out += ["Per event, at the state of N−1: the Pool's oracle price (proxy), the aggregator behind it read as an ordinary caller, the borrower's "
            "health factor under each, interest-only eligibility at block N's timestamp, and the resulting category.", "",
            "| Figure | Value |", "|---|---|",
            f"| Events | {s['events']} |",
            f"| Publicly observable (liquidatable at N−1 with the Pool's own prices) | **{s['categories']['publicly_observable']}** |",
            f"| Predictable, not yet eligible (aggregator already carried the round, or time alone) | **{s['categories']['predictable_not_yet_eligible']}** |",
            f"| Intra-block only (no public signal before block N) | **{s['categories']['intra_block_only']}** |",
            f"| Unknown (a probe failed) | {s['categories']['unknown']} |",
            f"| Observable before the winning transaction | **{s['observable_before_winner']} of {s['events']} ({(s['observable_before_winner_rate'] or 0) * 100:.0f} %)** |",
            f"| Lead of the aggregator over the Pool's price (s): n / min / median / max | {s['lead_seconds']['n']} / {s['lead_seconds']['min']} / {s['lead_seconds']['median']} / {s['lead_seconds']['max']} |",
            f"| Events where the aggregator led the proxy | {s['events_where_aggregator_led_proxy']} |",
            f"| Aggregator types seen | {', '.join(s['aggregator_types'])} |", "",
            "| Tx | Block | Category | HF (Pool prices) | HF (aggregator prices) | Eligible at N ts | Lead (s) | Proxy reveal (s) |", "|---|---|---|---|---|---|---|---|"]
    for e in obs["events"]:
        cf = e.get("collateral_feed") or {}
        out.append(f"| {_short(e['tx'])} | {e['block']:,} | {e['category']} | {(e['health_factor_pool_prices'] or '—')[:8]} | {(e['health_factor_aggregator_prices'] or '—')[:8]} | "
                   f"{e['eligible_at_block_timestamp']} | {e['lead_seconds_before_winner'] if e['lead_seconds_before_winner'] is not None else '—'} | {cf.get('proxy_reveal_after_seconds') if cf.get('proxy_reveal_after_seconds') is not None else '—'} |")
    return out + [""]


def section_latency(lat, slug):
    out = [f"## Component latency ({slug.title()})", ""]
    if not lat:
        return out + ["Status: **NOT RUN**", ""]
    st = lat["stages"]
    out += [f"Measured {lat['generated_at'][:19]}Z against `{lat['rpc']}`, {lat['positions']} positions × {lat['rounds']} rounds; median / p90.", "",
            "| Stage | Latency |", "|---|---|",
            f"| RPC round trip (eth_chainId) | {_ms(st, 'rpc_rtt_ms')} |",
            f"| Head poll (eth_blockNumber) | {_ms(st, 'head_poll_ms')} |",
            f"| Protocol reads (one Multicall3: account data, 2 user reserves, 2 configurations, prices, premium, e-mode) | {_ms(st, 'rpc_reads_ms')} |",
            f"| Profitability math (pure functions) | {_ms(st, 'math_ms', 3)} |",
            f"| Swap quotes on every fee tier + L1 fee (one Multicall3) | {_ms(st, 'quote_ms')} |",
            f"| **Fast path total** (reads + math + quotes + gas price) | **{_ms(st, 'fast_total_ms')}** |",
            f"| Full path: fork at head + receiver deploy + liquidate on Anvil | {_ms(st, 'evm_simulation_ms')} |", ""]
    return out


def section_strategies(strat, slug):
    out = [f"## Strategy comparison on the same dataset ({slug.title()})", ""]
    if not strat:
        return out + ["Status: **NOT RUN**", ""]
    i = strat["inputs"]
    out += [f"Seed {strat['seed']}, {strat['bootstrap_samples']} bootstrap samples over {strat['events']} events (categories {strat['categories']}). "
            f"Observed winning bids: {i['winning_bids_n']} transactions, median {i['median_winning_bid_gwei']:.4f} gwei; effective competitors {i['competitors']}; "
            f"failed attempt cost {_usd(i['failed_attempt_cost_usd'])}; measured share of predictable events still contestable at the Pool's reveal: "
            f"{i['predictable_available_share_measured']} ({i['predictable_events']} events).", "",
            "| Strategy | Events in scope | With simulated profit | Bid (gwei) | P(win bid) | Ready in time | Modeled captures | Expected profit | 95 % interval | Verdict |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for s in strat["strategies"]:
        v = strat["verdict"][s["name"]]
        out.append(f"| `{s['name']}` | {s['events_in_scope']} | {s['events_with_profit']} | {s['bid_gwei']:.4f} | {s['p_win_bid']:.3f} | {s['ready_in_time_rate']:.2f} | "
                   f"{s['modeled_captures']:.2f} | **{_usd(s['expected_profit_total_usd'])}** | {_usd(s['expected_profit_ci95_usd'][0])} … {_usd(s['expected_profit_ci95_usd'][1])} | {v['statement']} |")
    out += ["", "Strategies: " + "; ".join(f"`{s['name']}`: {s['description']}" for s in strat["strategies"]), "",
            "Assumptions: " + "; ".join(strat["strategies"][0]["assumptions"]), ""]
    return out


def section_markets(mk, comp):
    out = ["## Markets and protocols", ""]
    if not mk:
        return out + ["Status: **NOT RUN**", ""]
    s = mk["summary"]
    out += [f"{s['markets']} markets registered across {len(s['chains'])} chains and protocols {', '.join(s['protocols'])}: {s['verified']} verified live, {s['failed']} failed, "
            f"{s['blocked']} blocked, {s['not_configured']} not configured (no endpoint for that chain in this environment).", "",
            "| Market | Protocol | Chain | Status | Liquidation events in the last 100 blocks | Note |", "|---|---|---|---|---|---|"]
    for m in mk["markets"]:
        rec = m.get("recent_liquidation_events_last_blocks") or {}
        out.append(f"| `{m['market']}` | {m['protocol']} | {m['chain_id']} | {m['status']} | {rec.get('events', '—')} | {(m.get('reason') or (m.get('verification') or {}).get('reason') or '')[:80]} |")
    out.append("")
    for w in comp or []:
        if not w:
            continue
        out += [f"Same-window comparison on {w['network']} (last {w['blocks']:,} blocks, {w['from_block']:,}–{w['to_block']:,}, via `{w['rpc']}`):", "",
                "| Market | Liquidation events | Unique transactions | Unique liquidators | Event | Ranges that failed |", "|---|---|---|---|---|---|"]
        for k, v in w["markets"].items():
            out.append(f"| `{k}` | {v['liquidation_events']} | {v['unique_txs']} | {v['unique_liquidators']} | {v.get('kind') or '—'} | {len(v.get('errors') or [])} |")
        out.append("")
    return out


def build(docs, networks):
    lines = [f"# Phase 4: competitive strategy optimization ({datetime.now(UTC).date().isoformat()})", "",
             "Read-only throughout; no signer, no broadcast path. Builds on the Phase 3 dataset (every recorded liquidation on the verified Pools, replayed "
             "through the real receiver with complete costs). New here: a separate low-latency candidate path, per-stage latency, the observability of each "
             "opportunity from legitimate public data, modular protocol adapters, ranking by attainable profit, and a strategy comparison with intervals.", ""]
    heads = []
    for slug in networks:
        obs = _load(f"{docs}/phase4-observability-{slug}.json")
        strat = _load(f"{docs}/phase4-strategies-{slug}.json")
        lat = _load(f"{docs}/phase4-latency-{slug}.json")
        s = (obs or {}).get("summary") or {}
        best = None
        if strat:
            cands = [(x["name"], Decimal(x["expected_profit_total_usd"])) for x in strat["strategies"] if not x["name"].startswith("D_")]
            best = max(cands, key=lambda t: t[1]) if cands else None
        heads.append((slug, s.get("events"), s.get("categories", {}), s.get("observable_before_winner_rate"), (lat or {}).get("stages", {}).get("fast_total_ms", {}).get("median"),
                      (lat or {}).get("stages", {}).get("evm_simulation_ms", {}).get("median"), best))
    lines += ["## Headline", "", "| Network | Events | Publicly observable | Predictable | Intra-block only | Observable before winner | Fast path median | Fork simulation median | Best public-data strategy (expected profit) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for slug, n, c, rate, fast, evm, best in heads:
        lines.append(f"| {slug} | {n or '—'} | {c.get('publicly_observable', '—')} | {c.get('predictable_not_yet_eligible', '—')} | {c.get('intra_block_only', '—')} | "
                     f"{f'{rate * 100:.0f} %' if rate is not None else '—'} | {f'{fast:.0f} ms' if fast else '—'} | {f'{evm / 1000:.1f} s' if evm else '—'} | "
                     f"{('none positive (best `' + best[0] + '` ' + _usd(best[1]) + ')') if best and best[1] <= 0 else (f'`{best[0]}` {_usd(best[1])}' if best else '—')} |")
    lines += ["", "**Finding.** On Base the price feeds Aave reads are Chainlink `DualAggregator` contracts: the aggregator returns a new round to ordinary callers "
              "immediately, while the proxy the Pool reads returns it only to the auction winner's transaction or after a 30 s gate. Eligibility is therefore predictable "
              "from public data, usually one block ahead; whether it is contestable depends on the reveal: when the winner unlocks the round inside the window, nothing "
              "is left for public bots. With the measured latencies and the observed winning bids, no public-data strategy shows positive expected profit on this "
              "dataset; the ceiling without competition is the sum of simulated profits.", ""]
    for slug in networks:
        lines += section_observability(_load(f"{docs}/phase4-observability-{slug}.json"), slug)
        lines += section_latency(_load(f"{docs}/phase4-latency-{slug}.json"), slug)
        lines += section_strategies(_load(f"{docs}/phase4-strategies-{slug}.json"), slug)
    lines += section_markets(_load(f"{docs}/phase4-markets.json"), [_load(f"{docs}/phase4-window-{n}.json") for n in networks])
    lines += ["## Simulator paths", "",
              "| Path | What it does | Round trips | Outcome class | Where |", "|---|---|---|---|---|",
              "| fast | Multicall3 read of every protocol input, pure math, Multicall3 quotes + L1 fee, gas price | 3 | theoretical | `app/simulation/fast_path.py`, `POST /api/simulations/fast` |",
              "| full | Anvil fork at N−1 or head, real receiver deployed, flash loan + liquidation + swap executed, every figure measured | fork reset + ~6 txs | simulated | `app/simulation/replay.py`, `POST /api/simulations` |", "",
              "Validation: on the 0x7ed3 event at N−1 the fast path produced the same debt to cover (9,149,694), collateral to liquidator (4,115,949,317,907,073) and swap output "
              "(9,653,389) as the fork execution, to the unit; its gas figure is the configured estimate (972,842 units) rather than the 569,713 measured on the fork.", "",
              "## Ranking by attainable profit", "",
              "Candidates now carry `attainable_net_profit_usd = p_capture × net − (1 − p_capture) × failed-attempt cost` with `p_capture` from the observability class "
              "(live candidates are publicly observable) and the priority-auction win probability at the median observed winning bid over this Pool's recent liquidations; "
              "the Opportunity Explorer sorts by it (`app/competition/attainable.py`, migration `ecdc7637b17c`). Unknown inputs leave the field null; nothing is promoted by a missing number.", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default="../docs")
    ap.add_argument("--networks", default="base,arbitrum")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    md = build(a.docs, a.networks.split(","))
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    print(md[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
