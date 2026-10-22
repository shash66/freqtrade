"""Assemble the Phase 3 report (docs/phase3-results.md) from the JSON artefacts produced by the Phase 3 tools.

Inputs (any may be missing; the section then says so instead of inventing numbers):
  docs/phase3-replays-<net>.json           validation runner, --batch phase3-<net>
  docs/phase3-theoretical-<net>.json       app.validation.theoretical
  docs/phase3-reconstruction-<net>-<block>.json   app.simulation.reconstruct
  docs/phase3-benchmark-<net>.json         app.competition.benchmark
  docs/phase3-bidmodel-<net>.json          app.competition.bidmodel
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import UTC, datetime
from decimal import Decimal


def _load(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _item(doc: dict | None, key: str) -> dict | None:
    if not doc:
        return None
    return next((i for i in doc.get("items", []) if i.get("key") == key), None)


def _short(h: str | None) -> str:
    return f"`{h[:6]}…{h[-4:]}`" if h else "—"


def _usd(x) -> str:
    if x is None:
        return "—"
    d = Decimal(str(x))
    return f"${d:,.2f}" if abs(d) >= 1 else f"${d:.4f}"


def _fmt_raw(raw, dec: int) -> str:
    if raw is None:
        return "—"
    return f"{Decimal(int(raw)) / Decimal(10**dec):,.6f}"


def section_replays(replays: dict | None, slug: str) -> list[str]:
    out = [f"## Replays through the real flash-loan receiver ({slug.title()})", ""]
    it = _item(replays, "replay")
    if it is None or it.get("status") != "EXECUTED":
        out.append(f"Status: **{it['status'] if it else 'NOT RUN'}** — {it.get('reason') if it else 'no replay JSON found'}")
        return out + [""]
    d = it["detail"]
    tri = (_item(replays, "triage") or {}).get("detail", {})
    out += [f"Batch `{d['batch']}`, generated {replays['generated_at'][:19]}Z. Every row is one `LiquidationCall` **event**; a transaction "
            "with several events yields several replays, and each event's outcome is taken from the final fork attempt of its run.", "",
            "| Count | Value |", "|---|---|",
            f"| Unique historical transactions | {d['unique_historical_txs']} |",
            f"| Liquidation events (replays) | {d['liquidation_events']} |",
            f"| Simulation attempts (fork attempts incl. retries) | {d['simulation_attempts']} (events with a retry: {d['events_with_retry']}) |",
            f"| Execution paths | {d['execution_paths']} |",
            f"| Succeeded on the fork | {d['succeeded']} |",
            f"| … of which net profitable with **complete** costs | **{d['profitable']}** |",
            f"| … unprofitable after complete costs | {d['unprofitable_after_costs']} |",
            f"| … succeeded but a material cost unknown (never labeled profitable) | {d['succeeded_with_incomplete_costs']} |",
            f"| Reverted | {d['reverted']} |",
            f"| Not liquidatable at N−1 (block boundary) | {d['not_eligible_at_block_boundary']} (liquidatable at block N's timestamp before its transactions: {d['eligible_at_execution_only']}) |",
            f"| Eligibility (fork, execution) | {d['eligibility']} |",
            f"| Net simulated profit, complete costs only | **{_usd(d['net_profit_usd_total_complete'])}** |",
            f"| Fee sources | {d['fee_sources']} |",
            "", f"Failure triage: {tri.get('classes', {})}", ""]
    ok = [r for r in d["results"] if r["success"]]
    if ok:
        out += ["Successful replays (measured on the fork; all costs listed; `complete` = every material cost known):", "",
                "| Tx | Block | Eligible N−1 / exec | Repaid (raw) | Swap out (raw) | Premium (raw) | Surplus (raw) | Gas | L2+L1 fee (USD) | Net profit | Complete |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in ok:
            rec = r.get("reconciliation") or {}
            out.append(f"| {_short(r['tx'])} | {r['block']:,} | {r['eligible_at_fork']} / {r.get('eligible_at_execution')} | {r['debt_repaid_raw']} | {r['swap_out_raw']} | "
                       f"{r['premium_raw']} | {r.get('surplus_raw')} | {r['gas_used']:,} | {_usd(rec.get('fee_usd') or r.get('gas_cost_usd'))} | "
                       f"**{_usd(r['net_profit_usd'])}** | {r.get('profit_complete')} |")
        out.append("")
    return out


def section_theoretical(theo: dict | None, slug: str) -> list[str]:
    out = [f"## Verified theoretical profit of the actual winners ({slug.title()})", ""]
    if not theo or theo.get("status") != "EXECUTED":
        return out + [f"Status: **{theo.get('status') if theo else 'NOT RUN'}** — {theo.get('reason') if theo else 'no JSON'}", ""]
    t = theo["totals"]
    out += ["Outcome class **theoretical**: seized collateral and repaid debt from the on-chain event, valued at the Aave oracle prices of "
            "the winning block (archive read), minus the winner's receipt-verified fee (once per transaction). This is what the protocol "
            "paid out before any swap; it is an upper bound, not something this system earned or simulated.", "",
            "| Figure | Value |", "|---|---|",
            f"| Events / unique transactions | {t['events']} / {t['unique_txs']} |",
            f"| Theoretical gross (oracle value of bonus) | **{_usd(t['theoretical_gross_usd'])}** |",
            f"| Theoretical net after winners' verified fees | **{_usd(t['theoretical_net_usd_verified_fees'])}** (fees verified on {t['events_with_verified_fee']} events) |", ""]
    big = sorted(theo["events"], key=lambda e: Decimal(e.get("theoretical_gross_usd") or 0), reverse=True)[:5]
    out += ["Largest events:", "", "| Tx | Block | Repaid (USD) | Seized (USD) | Gross | Winner fee | Net |", "|---|---|---|---|---|---|---|"]
    for e in big:
        out.append(f"| {_short(e['tx'])} | {e['block']:,} | {_usd(e.get('debt_repaid_usd'))} | {_usd(e.get('collateral_seized_usd'))} | {_usd(e.get('theoretical_gross_usd'))} | "
                   f"{_usd(e.get('winner_fee_usd'))} | {_usd(e.get('theoretical_net_usd'))} |")
    return out + [""]


def section_reconstruction(recs: list[dict]) -> list[str]:
    out = ["## Intra-block reconstruction", ""]
    if not recs:
        return out + ["Status: **NOT RUN** — no reconstruction JSON found.", ""]
    for r in recs:
        trig = r.get("trigger") or {}
        out += [f"### Block {r['block_number']:,} ({r['network']}), liquidation {_short(r.get('liquidation_tx_hash'))}", "",
                f"Fork at N−1 with block N's timestamp pinned; {r['replayed']} of {r['tx_count']} transactions re-executed in order "
                f"({r['skipped_system_txs']} system tx skipped, {r.get('skipped_irrelevant_txs', 0)} skipped as unable to touch Aave/oracle state, "
                f"{r['status_mismatches']} status mismatches).", "",
                "| Observation | Value |", "|---|---|",
                f"| Health factor at N−1 (boundary) | {r['health_factor_at_boundary']} (eligible: {r['eligible_at_boundary']}) |",
                f"| Health factor at block N's timestamp, before its transactions | {r['health_factor_at_timestamp']} (eligible: {r['eligible_at_timestamp']}) |",
                f"| Prices at N−1 (collateral / debt, oracle raw) | {r['prices_at_boundary'].get('collateral')} / {r['prices_at_boundary'].get('debt')} |",
                f"| Trigger transaction | index {trig.get('index')} {_short(trig.get('tx_hash'))} — **{trig.get('kind')}** {trig.get('evidence')} |",
                f"| Health factor before → after trigger | {trig.get('health_factor_before')} → {trig.get('health_factor_after')} |",
                f"| Collateral price before → after trigger | {trig.get('collateral_price_before')} → {trig.get('collateral_price_after')} |",
                f"| Winning liquidation index in block | {r.get('liquidation_tx_index')} |",
                f"| Transactions between trigger and liquidation | {r.get('txs_between_trigger_and_liquidation')} |",
                f"| Health factor right before the liquidation | {r.get('health_factor_before_liquidation')} (eligible: {r.get('eligible_before_liquidation')}) |"]
        tg = r.get("oracle_time_gate") or {}
        for a, v in (tg.get("assets") or {}).items():
            if v.get("reveal_after_seconds") is not None:
                out.append(f"| Time-gated round at N−1 for {_short(a)} | public price {v['price_at_boundary']} → hidden round {v['revealed_price']}, revealed {v['reveal_after_seconds']} s after block N's timestamp with no transaction |")
        sim = r.get("simulated_at_position")
        if sim:
            out.append(f"| Our receiver executed at that exact position | status {sim.get('status')} surplus {sim.get('surplus_raw')} gas {sim.get('gas_used')} {sim.get('error') or sim.get('revert_reason') or ''} |")
        out += ["", "Transactions that changed the position or the two prices (health factor and oracle prices read after each):", "",
                "| # | Tx | Kind | HF after | Collateral price | Debt price | Note |", "|---|---|---|---|---|---|---|"]
        for c in r.get("changes", [])[:40]:
            out.append(f"| {c['index']} | {_short(c['tx_hash'])} | {c['kind']} {'; '.join(c.get('evidence', [])[:2])} | {c['health_factor']} | {c['collateral_price']} | {c['debt_price']} | {c.get('note') or ''} |")
        out += ["", "Assumptions: " + "; ".join(r.get("assumptions", [])), ""]
        if r.get("warnings"):
            out += ["Warnings: " + "; ".join(r["warnings"]), ""]
    return out


def section_timegate(tg: dict | None, slug: str) -> list[str]:
    out = [f"## Time-gated oracle rounds at N−1 ({slug.title()})", ""]
    if not tg:
        return out + ["Status: **NOT RUN** — no time-gate JSON found.", ""]
    sm = tg["summary"]
    out += ["Probe: fork at N−1, apply **no** transaction, advance only the block timestamp from block N's timestamp in 5 s steps and read the "
            "Aave oracle prices of the event's two reserves. A price that moves with time alone means the feed already stored a newer round at "
            "N−1 and only reveals it after a delay (a time-gated / SVR-style aggregator). `reveal_after_seconds = 0` means the round became "
            "public exactly at block N's timestamp.", "",
            "| Figure | Value |", "|---|---|",
            f"| Events / unique blocks probed | {sm['events']} / {sm['unique_blocks_probed']} |",
            f"| Events whose collateral or debt feed held a hidden newer round at N−1 | **{sm['events_with_time_gated_round_at_n_minus_1']}** |",
            f"| Reveal delays observed (s after block N's timestamp) | {sm['reveal_seconds']} |", "",
            "| Tx | Block | Asset | Price at N−1 (public) | Hidden round price | Public after (s) |", "|---|---|---|---|---|---|"]
    for e in tg["events"]:
        for a, v in (e["probe"].get("assets") or {}).items():
            if v.get("reveal_after_seconds") is not None:
                out.append(f"| {_short(e['tx'])} | {e['block']:,} | {_short(a)} | {v['price_at_boundary']} | {v['revealed_price']} | {v['reveal_after_seconds']} |")
    return out + [""]


def section_benchmark(b: dict | None) -> list[str]:
    out = ["## Competitive shadow benchmark (live, read-only)", ""]
    if not b:
        return out + ["Status: **NOT RUN** — no benchmark JSON found.", ""]
    s = b["summary"]

    def st(d):
        return f"n={d['n']}, median {d['median']:.3f} s, p90 {d['p90']:.3f} s, max {d['max']:.3f} s" if d and d.get("n") else "no samples"
    out += [f"Window: {b['duration_seconds'] / 60:.1f} min from {b['started_at'][:19]}Z on {b['network']} via `{b['rpc'].split('//')[-1].split('/')[0]}`; "
            f"watch set {b['watch_set_size']} positions (health factor < {b['watch_hf_max']} at last reconcile); blocks observed {b['blocks_observed']} "
            f"({b['first_block']:,} → {b['last_block']:,}).", "",
            "| Measure (observed) | Value |", "|---|---|",
            f"| Block observation lag (wall clock − block timestamp; includes clock skew + polling) | {st(b['block_lag_wallclock_seconds'])} |",
            f"| Detection latency (head seen → all watched health factors re-read, {b['multicall_batch_size']} Multicall3 round trip(s)) | {st(b['detection_latency_seconds'])} |",
            f"| Simulation latency (fork at head + receiver deploy + liquidate on private Anvil) | {st(b['simulation_latency_seconds'])} |",
            f"| Candidates that became liquidatable while watching | {s['candidates_total']} (liquidated by a competitor: {s['liquidated_by_competitor']}, healed: {s['healed']}, still open: {s['still_open']}) |",
            f"| Blocks alive before a competitor took them | {s['blocks_alive_when_taken']} |",
            f"| Competitor `LiquidationCall` transactions in the window | {s['competitor_txs']} (same block as our detection: {s['competitor_txs_same_block_as_our_detection']}; before we ever flagged the borrower: {s['competitor_txs_before_our_detection']}; unique liquidators: {s['unique_liquidators']}) |",
            f"| Competitor priority fee / effective gas price (median, wei) | {s['median_competitor_priority_fee_wei']} / {s['median_competitor_effective_gas_price_wei']} |", ""]
    if b.get("competitor_txs"):
        out += ["| Block | Tx | Liquidator | Borrower | In watch set | Blocks after our detection | Gas price (wei) |", "|---|---|---|---|---|---|---|"]
        for x in b["competitor_txs"][:20]:
            out.append(f"| {x['block_number']:,} | {_short(x['tx_hash'])} | {_short(x['liquidator'])} | {_short(x['borrower'])} | {x['in_watch_set']} | {x['blocks_after_our_detection']} | {x['effective_gas_price_wei']} |")
        out.append("")
    if b.get("simulations"):
        out += ["Simulation latency probes:", "", "| Block | Borrower | Seconds | Outcome |", "|---|---|---|---|"]
        for x in b["simulations"]:
            out.append(f"| {x.get('block')} | {_short(x.get('borrower'))} | {x['seconds']:.2f} | {x.get('revert_reason') or x.get('error') or ('success' if x.get('success') else '')} |" if x.get("seconds") is not None
                       else f"| {x.get('block')} | {_short(x.get('borrower'))} | — | {x.get('error')} |")
        out.append("")
    out += ["Assumptions: " + "; ".join(b.get("assumptions", [])), ""]
    if b.get("warnings"):
        out += [f"Warnings ({len(b['warnings'])}): " + "; ".join(b["warnings"][:5]), ""]
    return out


def section_bidmodel(bm: dict | None) -> list[str]:
    out = ["## Execution bids and win probability (observed vs modeled)", ""]
    if not bm:
        return out + ["Status: **NOT RUN** — no bid-model JSON found.", ""]
    o, md, t = bm["observed"], bm["modeled"], bm["totals"]
    g = o["winning_effective_gas_price_wei"]
    out += ["**Observed** (on-chain winners on the verified Pool):", "",
            "| Measure | Value |", "|---|---|",
            f"| Liquidation transactions / events / unique liquidators | {o['liquidation_txs']} / {o['liquidation_events']} / {o['unique_liquidators']} |",
            f"| Liquidator shares (top 5) | {o['liquidator_shares_top5']} |",
            f"| Winning effective gas price (wei): p25 / median / p75 / p90 / max | {g['p25']} / {g['median']} / {g['p75']} / {g['p90']} / {g['max']} |",
            f"| Winning gas used: median / p90 | {o['winning_gas_used']['median']} / {o['winning_gas_used']['p90']} |",
            f"| Winning L1 data fee (wei, median, receipt-verified) | {o['winning_l1_fee_wei']['median']} |", "",
            "**Modeled** (assumptions listed below):", "",
            "| Parameter | Value |", "|---|---|",
            f"| Effective competitors ready per opportunity | {md['effective_competitors_ready']} |",
            f"| Our gas units (median measured on successful replays) | {md['our_gas_units']:,} |",
            f"| Our ready time assumed (detection p90 + simulation median from the benchmark) | {md['our_ready_seconds_assumed']:.2f} s |",
            f"| P(win) FCFS vs observed competitor reactions ({md['competitor_reaction_samples']} samples) | {md['p_win_fcfs_vs_observed_competitor_reaction']} |",
            f"| Median simulated profit per profitable event | {_usd(md['median_simulated_profit_usd'])} |", "",
            "Bid curve (priority-auction model; expected profit per attempt at the median simulated profit):", "",
            "| Bid | gwei | Our gas cost | P(win) | E[profit per attempt] |", "|---|---|---|---|---|"]
    for p in bm["bid_curve"]:
        out.append(f"| {p['label']} | {p['bid_gwei']:.4f} | {_usd(p['gas_cost_usd'])} | {p['p_win_priority_auction']:.3f} | {p['expected_profit_usd_priority_auction'] if p['expected_profit_usd_priority_auction'] == 'n/a' else _usd(p['expected_profit_usd_priority_auction'])} |")
    out += ["", "Per-event valuation totals:", "", "| Bucket | Events | Simulated profit | Competition-adjusted (FCFS) | Competition-adjusted (priority auction) |", "|---|---|---|---|---|",
            f"| Observable at the block boundary | {t['events_boundary_observable']} | {_usd(t['boundary_observable_simulated_profit_usd'])} | {_usd(t['competition_adjusted_fcfs_usd']) if t['competition_adjusted_fcfs_usd'] is not None else 'not estimable (no reaction samples)'} | {_usd(t['competition_adjusted_priority_auction_usd'])} |",
            f"| Only liquidatable inside the winning block | {t['events_intra_block_only']} | {_usd(t['intra_block_only_simulated_profit_usd'])} | $0 (not capturable by boundary polling) | $0 |",
            f"| Not simulated profitably / cost incomplete | {t['events_not_profitable_or_incomplete']} | — | — | — |", "",
            "Assumptions: " + "; ".join(bm.get("assumptions", [])), ""]
    if bm.get("warnings"):
        out += ["Warnings: " + "; ".join(bm["warnings"]), ""]
    return out


def build(docs_dir: str, networks: list[str]) -> str:
    lines = [f"# Phase 3: profit verification and competitive execution research ({datetime.now(UTC).date().isoformat()})", "",
             "Read-only throughout: no signer, no broadcast path; every execution happened on a private Anvil fork. Four outcome classes are kept apart: "
             "**theoretical** (protocol math at the winning block), **simulated** (our receiver on a fork), **modeled** (competition-adjusted expectation "
             "from stated assumptions) and **onchain_actual** (what competitors did). Raw data for every table is in the JSON files named in each section.", ""]
    summary_rows = []
    for slug in networks:
        replays = _load(os.path.join(docs_dir, f"phase3-replays-{slug}.json"))
        theo = _load(os.path.join(docs_dir, f"phase3-theoretical-{slug}.json"))
        bm = _load(os.path.join(docs_dir, f"phase3-bidmodel-{slug}.json"))
        rd = (_item(replays, "replay") or {}).get("detail") or {}
        tt = (theo or {}).get("totals") or {}
        bt = (bm or {}).get("totals") or {}
        summary_rows.append((slug, tt.get("theoretical_gross_usd"), tt.get("theoretical_net_usd_verified_fees"), rd.get("net_profit_usd_total_complete"),
                             rd.get("profitable"), rd.get("liquidation_events"), bt.get("competition_adjusted_priority_auction_usd"), bt.get("competition_adjusted_fcfs_usd"),
                             bt.get("intra_block_only_simulated_profit_usd")))
    lines += ["## Headline figures", "", "| Network | Verified theoretical gross / net (winners, oracle-valued) | Successfully simulated profit (complete costs) | Competition-adjusted expected profit (priority auction / FCFS) | Simulated profit only reachable inside the winning block |",
              "|---|---|---|---|---|"]
    for slug, g, n, sim, prof, ev, pa, fc, intra in summary_rows:
        lines.append(f"| {slug} | {_usd(g)} / {_usd(n)} | {_usd(sim)} ({prof if prof is not None else '—'} of {ev if ev is not None else '—'} events) | "
                     f"{_usd(pa)} / {_usd(fc) if fc is not None else 'not estimable'} | {_usd(intra)} |")
    lines += ["", "The three profit figures answer different questions: what the protocol paid the actual winners; what our receiver realised on a fork "
              "with every cost known; and what a boundary-polling bot could expect after competition. None of them is money earned.", ""]
    for slug in networks:
        lines += section_theoretical(_load(os.path.join(docs_dir, f"phase3-theoretical-{slug}.json")), slug)
        lines += section_replays(_load(os.path.join(docs_dir, f"phase3-replays-{slug}.json")), slug)
    recs = [_load(p) for p in sorted(glob.glob(os.path.join(docs_dir, "phase3-reconstruction-*.json")))]
    lines += section_reconstruction([r for r in recs if r])
    for slug in networks:
        if os.path.exists(os.path.join(docs_dir, f"phase3-timegate-{slug}.json")):
            lines += section_timegate(_load(os.path.join(docs_dir, f"phase3-timegate-{slug}.json")), slug)
    lines += section_benchmark(_load(os.path.join(docs_dir, "phase3-benchmark-base.json")))
    lines += section_bidmodel(_load(os.path.join(docs_dir, "phase3-bidmodel-base.json")))
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
    print(md[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
