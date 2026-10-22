"""Assemble docs/phase5-results.md (Phase 5 multi-strategy profitability comparison) from the plugin artefacts
`docs/phase5-<strategy>-<network>-<mode>.json`, the Compound V3 event history and the unified ranking.
Missing inputs are reported as NOT RUN, never filled in; every USD figure keeps its outcome class."""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median

from app.plugins.ranking import load_artefacts, rank

STRATEGY_TITLES = {"compound_v3": "Strategy A — Compound V3 liquidations", "dex_arb": "Strategy B — DEX arbitrage", "yield_opt": "Strategy C — Yield optimisation"}


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _usd(x):
    if x is None or x == "":
        return "—"
    d = Decimal(str(x))
    sign = "−" if d < 0 else ""
    d = abs(d)
    return f"{sign}${d:,.2f}" if d >= 1 else f"{sign}${d:.4f}"


def _pct(x):
    return "—" if x is None else f"{Decimal(str(x)) * 100:.0f} %"


def _short(h):
    return f"`{h[:6]}…{h[-4:]}`" if h else "—"


def _wei_gwei(w):
    return "—" if w is None else f"{Decimal(w) / Decimal(10**9):.4f} gwei"


# ----------------------------------------------------------------------------- Compound V3
def section_compound(arts: dict[str, dict], net: str, history: dict | None) -> list[str]:
    out = [f"### {net.title()}", ""]
    disc = arts.get("simulate") or arts.get("discover")
    if not disc:
        return out + ["Status: **NOT RUN**", ""]
    rows = disc.get("rows", [])
    scans = [r["opportunity"] for r in rows if r["opportunity"]["state"].get("kind") == "scan"]
    reserves = [r for r in rows if r["opportunity"]["state"].get("kind") == "reserve_purchase"]
    absorbs = [r for r in rows if r["opportunity"]["state"].get("kind") == "absorb_and_buy"]
    out += [f"Discovery at block `{disc.get('block')}` ({disc.get('generated_at', '')[:19]} UTC, {disc.get('discovery_ms', 0) / 1000:.1f} s): borrowers are the senders of "
            "`Withdraw` events in the last 9,000 blocks (plus any configured accounts), checked with `isLiquidatable` and `borrowBalanceOf` through Multicall3; seized collateral "
            "for sale comes from `getCollateralReserves` priced at the Comet oracle with the `quoteCollateral` discount.", "",
            "| Market | Accounts scanned | Borrowers | Liquidatable now | Reserve lots | Largest reserve lot (gross at discount) |", "|---|---|---|---|---|---|"]
    markets = sorted({r["opportunity"]["key"].split(":")[0] for r in rows})
    for m in markets:
        sc = next((s for s in scans if s["key"].startswith(m)), None)
        st = (sc or {}).get("state", {}) if sc else next((r["opportunity"]["state"] for r in absorbs if r["opportunity"]["key"].startswith(m)), {})
        res = [r for r in reserves if r["opportunity"]["key"].startswith(m)]
        best = max(res, key=lambda r: Decimal(r["opportunity"]["gross_profit_usd"] or 0), default=None)
        liq = sum(1 for r in absorbs if r["opportunity"]["key"].startswith(m))
        out.append(f"| {m} | {st.get('accounts_scanned', '—')} | {st.get('borrowers_scanned', '—')} | **{liq}** | {len(res)} | "
                   f"{_usd(best['opportunity']['gross_profit_usd']) if best else '—'} ({best['opportunity']['state'].get('bonus_bps', '—')} bps) |" if best else
                   f"| {m} | {st.get('accounts_scanned', '—')} | {st.get('borrowers_scanned', '—')} | **{liq}** | {len(res)} | — |")
    out.append("")
    sims = [r for r in rows if r.get("simulation")]
    if sims:
        out += [f"Fork simulations ({len(sims)}; `CometLiquidator.sol`: flash loan of the base asset → `absorb` → `buyCollateral` → Uniswap v3 swap → repay):", "",
                "| Opportunity | Result | Gross | Net (complete?) | Measured | Time |", "|---|---|---|---|---|---|"]
        for r in sims:
            s = r["simulation"]
            out.append(f"| `{r['opportunity']['key'][:48]}` | {'success' if s['success'] else 'revert: ' + (s['revert_reason'] or '')[:60]} | {_usd(s['gross_profit_usd'])} | "
                       f"{_usd(s['net_profit_usd'])} ({'complete' if s['net_profit_complete'] else 'incomplete: ' + ', '.join(s['costs'].get('missing') or []) or 'no figure'}) | "
                       f"gas {s['costs'].get('gas_units') or '—'} | {r.get('simulation_ms', 0) / 1000:.0f} s |")
        out.append("")
    else:
        out += ["Fork simulations: none ran at this discovery (no liquidatable account and every reserve lot below the $0.05 floor).", ""]
    rep = (arts.get("replay") or {}).get("replay")
    if rep:
        out += [f"Historical replay over blocks {rep['from_block']:,}–{rep['to_block']:,}: {rep['sample_note']}.", "",
                "| Absorb tx | Block | Liquidatable at N−1? | Our receiver | Gross | Net (complete?) | Note |", "|---|---|---|---|---|---|---|"]
        for d in rep.get("details", []):
            sim = d.get("simulation") or {}
            out.append(f"| {_short(d.get('tx'))} | {d.get('block', '—')} | {d.get('liquidatable_at_n_minus_1', '—')} | "
                       f"{('success' if sim.get('success') else 'revert: ' + (sim.get('revert_reason') or d.get('error') or '')[:70]) if (sim or d.get('error')) else '—'} | "
                       f"{_usd(sim.get('gross_profit_usd'))} | {_usd(sim.get('net_profit_usd'))} ({'complete' if sim.get('net_profit_complete') else 'incomplete'}) | {(d.get('note') or '')[:80]} |")
        out.append("")
    else:
        out += ["Historical replay: **NOT RUN** (needs the event history and an Anvil fork).", ""]
    if history:
        out += section_comet_history(history)
    return out


def section_comet_history(h: dict) -> list[str]:
    out = [f"Competitor analysis from Comet events, blocks {h['from_block']:,}–{h['to_block']:,} ({(h['to_block'] - h['from_block'] + 1) / 43_200:.1f} days on Base):", ""]
    absorbs, buys = {}, []
    for mk, v in h.get("markets", {}).items():
        for a in v.get("absorbs", []):
            absorbs.setdefault(a["tx"], {"market": mk, **a})
        buys.extend({"market": mk, **b} for b in v.get("buys", []))
    out += ["| Figure | Value |", "|---|---|", f"| Absorb transactions (unique) | **{len(absorbs)}** |",
            f"| Distinct absorbers | {len({a['absorber'] for a in absorbs.values()})} |",
            f"| `buyCollateral` transactions | **{len(buys)}** (buyers: {len({b['buyer'] for b in buys})}) |"]
    gp = [a["effective_gas_price_wei"] for a in absorbs.values() if a.get("effective_gas_price_wei")]
    if gp:
        out.append(f"| Absorb effective gas price: median / max | {_wei_gwei(median(gp))} / {_wei_gwei(max(gp))} |")
    gl = [a["gas_used"] for a in absorbs.values() if a.get("gas_used")]
    if gl:
        out.append(f"| Absorb gas used: median | {median(gl):,.0f} |")
    ti = [a["tx_index"] for a in absorbs.values() if a.get("tx_index") is not None]
    if ti:
        out.append(f"| Absorb position in block (tx index): median | {median(ti):.0f} |")
    out.append("")
    if absorbs:
        out += ["| Absorb tx | Block | Market | Absorber | Collateral seized (USD) | Gas price |", "|---|---|---|---|---|---|"]
        for tx, a in sorted(absorbs.items(), key=lambda kv: kv[1]["block"]):
            out.append(f"| {_short(tx)} | {a['block']:,} | {a['market'].replace('compound_v3_', '')} | {_short(a['absorber'])} | {_usd(Decimal(a['usdValue']) / Decimal(10**8)) if a.get('usdValue') is not None else '—'} | {_wei_gwei(a.get('effective_gas_price_wei'))} |")
        out.append("")
    if buys:
        out += ["| Buy tx | Block | Market | Buyer | Base paid (raw) | Collateral (raw) | Gas price |", "|---|---|---|---|---|---|---|"]
        for b in sorted(buys, key=lambda x: x["block"]):
            out.append(f"| {_short(b['tx'])} | {b['block']:,} | {b['market'].replace('compound_v3_', '')} | {_short(b['buyer'])} | {b.get('baseAmount', '—')} | {b.get('collateralAmount', '—')} | {_wei_gwei(b.get('effective_gas_price_wei'))} |")
        out.append("")
    out += ["Reading: on Compound V3 the absorber is not paid; the protocol takes the collateral into reserves and anyone may buy it at the `quoteCollateral` discount. "
            "The race is therefore for `buyCollateral` right after the absorb (often in the same transaction, which is what `CometLiquidator.sol` does). "
            "Aave's close-factor / liquidation-bonus formulas do not apply; the discount is `storeFrontPriceFactor × (1 − liquidationFactor)` per asset.", ""]
    return out


# ----------------------------------------------------------------------------- DEX arbitrage
def section_dex(arts: dict[str, dict], net: str) -> list[str]:
    out = [f"### {net.title()}", ""]
    disc = arts.get("simulate") or arts.get("discover")
    if not disc:
        return out + ["Status: **NOT RUN**", ""]
    rows = disc.get("rows", [])
    out += [f"Discovery at block `{disc.get('block')}` ({disc.get('generated_at', '')[:19]} UTC, {disc.get('discovery_ms', 0) / 1000:.1f} s): every directed venue pair of each registered pool "
            "set is quoted through Multicall3 (Uniswap v3 `QuoterV2`, Aerodrome `getAmountsOut`) on a 7-point geometric size grid from $50 to $50,000; the best quoted "
            "round trip net of the 5 bps Aave flash premium is kept.", "",
            "| Directed pair | Size | Quoted gross | Premium | Net before gas | Executable? |", "|---|---|---|---|---|---|"]
    for r in rows:
        o = r["opportunity"]
        st = o["state"]
        out.append(f"| `{o['key']}` | {Decimal(st['size_raw']) / Decimal(10 ** int(st['dec_in'])):,.4f} | {_usd(o['gross_profit_usd'])} | {_usd(o['costs'].get('flash_premium_usd'))} | "
                   f"{_usd(st.get('net_pre_gas_usd'))} | {'yes' if st.get('executable') else 'no'} |")
    out.append("")
    sims = [r for r in rows if r.get("simulation")]
    if sims:
        out += [f"Fork simulations ({len(sims)}; `FlashArb.sol`: `flashLoanSimple(tokenIn)` → leg 1 → leg 2 → repay; validation runs are the best non-executable rows, "
                "run to measure what an attempt costs):", "",
                "| Directed pair | Validation? | Result | Round trip P&L | Flash premium | Gas (units, L2 cost, L1 fee) | Net of everything | Complete? |", "|---|---|---|---|---|---|---|---|"]
        for r in sims:
            s, c = r["simulation"], r["simulation"]["costs"]
            out.append(f"| `{r['opportunity']['key']}` | {'yes' if r.get('validation_run') else 'no'} | {'success' if s['success'] else 'reverted at settlement' if 'InsufficientProfit' in (s['revert_reason'] or '') else 'revert: ' + (s['revert_reason'] or '')[:50]} | "
                       f"{_usd(s['gross_profit_usd'])} | {_usd(c.get('flash_premium_usd'))} | {c.get('gas_units') or '—'}, {_usd(c.get('l2_execution_usd'))}, {_usd(c.get('l1_data_fee_usd'))} | "
                       f"**{_usd(s['net_profit_usd'])}** | {'yes' if s['net_profit_complete'] else 'no: ' + ', '.join(c.get('missing') or [])} |")
        out.append("")
    rep = (arts.get("replay") or {}).get("replay")
    if rep:
        out += [f"Historical sampling over blocks {rep['from_block']:,}–{rep['to_block']:,}: {rep['sample_note']}. Mean best net-before-gas spread per sampled block "
                f"{_usd(rep.get('expected_profit_per_attempt_usd'))}; worst {_usd(rep.get('worst_case_loss_usd'))}.", ""]
        vals = [Decimal(d["net_pre_gas_usd"]) for d in rep.get("details", []) if "net_pre_gas_usd" in d]
        if vals:
            pos = sum(1 for v in vals if v > 0)
            out += [f"Of {len(vals)} sampled blocks, {pos} had any positive net-before-gas spread (max {_usd(max(vals))}); the gas floor used was the fork-measured attempt cost.", ""]
    else:
        out += ["Historical sampling: **NOT RUN**.", ""]
    sh = (arts.get("shadow") or {}).get("shadow")
    if sh:
        series = sh.get("series") or []
        exe = sh.get("ticks_with_executable")
        best = [Decimal(s["best_gross_usd"]) for s in series if s.get("best_gross_usd") is not None]
        out += [f"Shadow monitoring (read-only, {sh['minutes']:g} min, {sh['ticks']} blocks observed): "
                + (f"{exe} blocks with an executable spread; " if exe is not None else "")
                + f"{sh['distinct_opportunities']} distinct executable opportunities"
                + (f"; best quoted gross over the window {_usd(max(best))}, median {_usd(median(best))}" if best else "") + ".", ""]
        pers = sh.get("persistence_blocks") or {}
        if pers:
            out += [f"Persistence of executable spreads: median {median(pers.values()):g} blocks (n={len(pers)}).", ""]
    else:
        out += ["Shadow monitoring: **NOT RUN**.", ""]
    return out


# ----------------------------------------------------------------------------- Yield
def section_yield(arts: dict[str, dict], net: str) -> list[str]:
    out = [f"### {net.title()}", ""]
    disc = arts.get("discover")
    if not disc:
        return out + ["Status: **NOT RUN**", ""]
    rows = disc.get("rows", [])
    out += [f"Live reads at block `{disc.get('block')}` ({disc.get('generated_at', '')[:19]} UTC). Figures are **passive yield** for a 10,000 USD position held 30 days at today's "
            "rates, net of an estimated $0.60 entry/exit gas; they are not transaction revenue and are never added to the simulated figures above.", "",
            "| Opportunity | Venue rates | 30-day gross | 30-day net | Risk | Notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        o = r["opportunity"]
        st = o["state"]
        if st.get("kind") == "stable_lending":
            venues = "; ".join(f"{v['venue']} {Decimal(v['supply_apy']) * 100:.2f} %" + ("" if v.get("enterable", True) else " (frozen)") for v in st.get("venues", []))
        else:
            venues = f"staking {Decimal(st.get('lst_apy', 0)) * 100:.2f} % / WETH borrow {Decimal(st.get('borrow_apy', 0)) * 100:.2f} % → net {Decimal(st.get('net_apy', 0)) * 100:.2f} % at {Decimal(st.get('leverage', 1)):.2f}×"
        out.append(f"| `{o['key'][:44]}` | {venues} | {_usd(o['gross_profit_usd'])} | **{_usd(o['net_profit_usd'])}** | {o['risk']} | {'; '.join(o.get('risk_notes', [])[:2])[:140]} |")
    out.append("")
    rep = (arts.get("replay") or {}).get("replay")
    if rep:
        stats = next((d["stats"] for d in rep.get("details", []) if "stats" in d), {})
        out += [f"Historical rates over blocks {rep['from_block']:,}–{rep['to_block']:,}: {rep['sample_note']}. Best stablecoin APY avg / min / max: "
                f"{stats.get('best_stable_apy_pct_avg', '—')} / {stats.get('best_stable_apy_pct_min', '—')} / {stats.get('best_stable_apy_pct_max', '—')} %; "
                f"expected 30-day net at the average: {_usd(rep.get('expected_profit_per_attempt_usd'))}.", ""]
    else:
        out += ["Historical rates: **NOT RUN**.", ""]
    return out


# ----------------------------------------------------------------------------- ranking / evaluation
def _ranking_line(r: dict) -> str:
    return (f"| {r['strategy']} | {r['network']} | {_usd(r['capital_usd'])} | {'yes' if r['flash_loan'] else 'no' if r['flash_loan'] is not None else '—'} | "
            f"{_usd(r['gross_profit_usd'])} ({r['gross_class']}) | {_usd(r['net_profit_complete_usd'])} ({r['net_class']}) | {_pct(r['p_capture'])}"
            f"{' (' + r['p_capture_model'].replace('_', ' ') + ')' if r['p_capture'] is not None and r['p_capture_model'] else ''} | "
            f"{_usd(r['attainable_profit_usd'])}{' (modeled)' if r['attainable_profit_usd'] is not None else ''} | {r['frequency_observed'][:90]} | {r['risk']} | "
            f"{', '.join(r['evidence_quality']) or 'none'} | **{r['verdict'].replace('_', ' ')}** |")


def section_ranking(rk: dict) -> list[str]:
    out = ["## Unified ranking (dashboard “Strategies” section)", "",
           "Every profit cell names its class: `theoretical` (protocol math or quotes), `simulated` (our receiver on a fork), `modeled` (capture probability applied), "
           "`passive_yield` (lending/staking rates). Classes are never summed. “Complete net profit” counts only simulations in which every material cost was measured.", "",
           "| Strategy | Network | Capital | Flash loan | Gross profit | Complete net profit | Capture probability | Attainable profit | Frequency (observed) | Risk | Evidence | Verdict |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    out.extend(_ranking_line(r) for r in rk["rows"])
    out += ["", "## Evaluation (§7)", "",
            "| Strategy / network | Opportunities (latest run) | Executable | Simulated (success) | Net profitable, complete | Lost to competition | Expected profit / attempt | Capital efficiency | Worst-case loss | Infra cost / month | Repeatability |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rk["rows"]:
        out.append(f"| {r['strategy']} / {r['network']} | {r['opportunities']}" + (f" (+{r['sample_size']['replay_opportunities']} historical)" if r['sample_size'].get('replay_opportunities') is not None else "")
                   + f" | {r['executable']} | {r['simulated']} ({r['simulated_success']}) | {r['net_profitable_complete']} | {r['lost_to_competition'] if r['lost_to_competition'] is not None else '—'} | "
                   f"{_usd(r['expected_profit_per_attempt_usd'])} | {r['capital_efficiency'] or '—'} | {_usd(r['worst_case_loss_usd'])} | ${r['infra_cost_usd_month']} (est.) | {r['repeatability'][:100]} |")
    out += ["", "Uncertainty and sample sizes:", ""]
    for r in rk["rows"]:
        ss = r["sample_size"]
        out.append(f"- **{r['strategy']} / {r['network']}**: {r['verdict_reason']}. Samples: {ss.get('opportunities')} opportunities, {ss.get('simulations')} simulations "
                   f"({ss.get('complete_simulations')} complete), {ss.get('replay_opportunities') if ss.get('replay_opportunities') is not None else 'no'} historical, "
                   f"{ss.get('shadow_ticks') if ss.get('shadow_ticks') is not None else 'no'} shadow ticks. " + ("; ".join(r["uncertainty"]) if r["uncertainty"] else ""))
    out.append("")
    return out


def build(docs: str, networks: list[str]) -> str:
    arts = load_artefacts(docs)
    rk = rank(docs)
    hist = {}
    for net in networks:
        merged = None
        for p in sorted(glob.glob(os.path.join(docs, f"phase5-comet-history-{net}*.json"))):
            h = _load(p)
            if not h:
                continue
            if merged is None:
                merged = {"from_block": h["from_block"], "to_block": h["to_block"], "markets": {k: {"absorbs": list(v["absorbs"]), "buys": list(v["buys"])} for k, v in h["markets"].items()}}
            else:
                merged["from_block"] = min(merged["from_block"], h["from_block"])
                merged["to_block"] = max(merged["to_block"], h["to_block"])
                for k, v in h["markets"].items():
                    m = merged["markets"].setdefault(k, {"absorbs": [], "buys": []})
                    m["absorbs"] += v["absorbs"]
                    m["buys"] += v["buys"]
        hist[net] = merged
    lines = ["# FlashLiquidator Lab — Phase 5 results: multi-strategy profitability research", "",
             f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `app.validation.phase5_report` from `docs/phase5-*.json`. "
             "Simulation only: the lab has no wallet, signer or transaction path; every “execution” is a private Anvil fork.", "",
             "## Question and answer", "",
             f"**Does any strategy show credible evidence of positive, realistically attainable net profit?** {rk['conclusion']['statement'].capitalize()}.", "",
             f"- Transaction strategies with positive modeled attainable net profit: {', '.join(rk['conclusion']['strategies_with_positive_attainable_net_profit']) or 'none'}.",
             f"- Passive-yield strategies with positive net yield (not transaction revenue): {', '.join(rk['conclusion']['strategies_with_positive_passive_yield']) or 'none'}.", "",
             "## Plugin framework", "",
             "Each strategy is a plugin (`backend/app/plugins/`) with the same interface: `discover` (opportunities now or at a block), `state`, `simulate` (the complete transaction on a fork), "
             "`competition` (capture probability with its model and assumptions), `replay` (historical evaluation) and `shadow_tick` (live read-only observation). "
             "`app.plugins.run` drives one strategy in one mode and writes the artefact; `app.plugins.ranking` builds the unified table; `/api/research/strategies` and the "
             "dashboard “Strategies” page read it. Configuration: `STRATEGY_<NAME>_<FIELD>` environment variables. Tests: `tests/test_plugins_*.py`.", ""]
    for strat, title in STRATEGY_TITLES.items():
        lines += [f"## {title}", ""]
        if strat == "compound_v3":
            lines += ["Market discovery uses the Phase 4 protocol registry (Comet markets per network); liquidation eligibility is Comet's own `isLiquidatable`, not Aave's health factor. "
                      "Profit comes only from buying seized collateral at the `quoteCollateral` discount (the absorber is unpaid).", ""]
        elif strat == "dex_arb":
            lines += ["Pools: Base WETH/USDC (Uniswap v3 0.05 % and 0.3 %, Aerodrome volatile), USDC/USDbC (Uniswap v3 0.01 %, Aerodrome stable), cbBTC pairs; Arbitrum WETH/USDC (0.05 %, 0.3 %), "
                      "USDC/USDT and USDC/USDC.e (0.01 %). Only pairs with two or more registered venues can arbitrage; spreads are live quotes at a block, never hypothetical.", ""]
        else:
            lines += ["Stablecoin lending across Aave V3 and Compound V3 and liquid-staking-token loops on Aave (supply LST, borrow WETH); staking yield is measured as the growth of "
                      "the LST/WETH oracle ratio over 7 days. Frozen or paused reserves are shown but never chosen.", ""]
        for net in networks:
            a = arts.get((strat, net), {})
            if strat == "compound_v3":
                lines += section_compound(a, net, hist.get(net))
            elif strat == "dex_arb":
                lines += section_dex(a, net)
            else:
                lines += section_yield(a, net)
    lines += section_ranking(rk)
    lines += ["## What this does and does not establish", "",
              "- Compound V3 on Base and Arbitrum had no liquidatable account and only dust reserves at every discovery; the historical window holds very few absorbs, so the "
              "competition model has almost no bids to learn from. The replay shows whether our receiver reproduces the recorded absorbs; it does not show a win rate.",
              "- DEX spreads between the registered venues were negative before gas at every quoted block, every size and in both directions; the validation simulations measure "
              "the full cost of an attempt (both legs executed, premium charged, gas of the reverted transaction). A spread that exists only inside a block (back-running a large "
              "swap) is invisible to this block-boundary sampling, and capturing it requires MEV infrastructure the lab does not model as attainable.",
              "- Yield figures are passive: they need capital on deposit, carry smart-contract, depeg and (for loops) liquidation risk, and float with utilisation. They are the only "
              "positive net figures in this phase and they are not transaction revenue.",
              "- Sample sizes are small (days, not months) and all figures come from public endpoints on free tiers; see `docs/limitations.md`.", "",
              "## Reproduce", "", "```bash", "scripts/phase5-research.sh base      # discover / simulate / replay / shadow for the three strategies, ranking, this report",
              "scripts/phase5-research.sh arbitrum", "```", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs"))
    ap.add_argument("--networks", default="base,arbitrum")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    md = build(os.path.abspath(a.docs), [n.strip() for n in a.networks.split(",") if n.strip()])
    out = a.out or os.path.join(os.path.abspath(a.docs), "phase5-results.md")
    with open(out, "w") as f:
        f.write(md)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
