"""Unified strategy ranking (Phase 5, §6–7): one row per strategy × network built only from the artefacts the
plugin runner writes to `docs/phase5-<strategy>-<network>-<mode>.json`.

The row carries the dashboard columns (capital, flash loan, gross profit, complete net profit, capture
probability, attainable profit, observed frequency, risk, evidence quality) and the evaluation metrics (§7:
opportunity counts, executable simulations, lost to competition, expected profit per attempt, capital
efficiency, worst-case loss, infrastructure cost, repeatability) with sample sizes and uncertainty notes.

Rules enforced here, not left to the reader:
  * every USD figure names its outcome class (`theoretical`, `simulated`, `modeled`, `passive_yield`); simulated
    profit is never added to anything observed on-chain and passive yield is never mixed with transaction revenue;
  * "complete net profit" counts only simulations whose every material cost was measured (`net_profit_complete`);
  * a capture probability without a model and its assumptions is reported as None, never as 1;
  * the verdict needs evidence: `positive_attainable` requires a complete, positive attainable figure from a fork
    simulation or a positive passive yield with its risks listed; anything else is `negative`, `not_estimable` or
    `no_opportunity`.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import median

from app.core.money import quantize_usd

BLOCKS_PER_DAY = {"base": 43_200, "arbitrum": 345_600}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
# Monthly infrastructure estimate for one always-on research node; an explicit assumption, overridable by env.
INFRA_COST_USD_MONTH_DEFAULT = Decimal("69")   # Alchemy Growth $49 + a small VPS $20 (estimate, see docs/phase5-results.md)


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _median(vals: list[Decimal]) -> Decimal | None:
    return quantize_usd(Decimal(str(median([float(v) for v in vals])))) if vals else None


@dataclass
class StrategyRow:
    strategy: str
    network: str
    kind: str
    description: str
    # dashboard columns
    capital_usd: Decimal | None              # own capital a taker must hold (median over opportunities)
    capital_note: str
    flash_loan: bool | None
    flash_loan_note: str
    gross_profit_usd: Decimal | None         # best opportunity, class in gross_class
    gross_class: str
    net_profit_complete_usd: Decimal | None  # best complete net profit, class in net_class
    net_class: str
    p_capture: Decimal | None
    p_capture_model: str | None
    p_capture_assumptions: list[str]
    attainable_profit_usd: Decimal | None    # p × net − (1 − p) × failed-attempt cost; modeled
    frequency_observed: str                  # human sentence with the window and the count
    frequency_per_day: Decimal | None
    risk: str
    evidence_quality: list[str]              # subset of historical / fork / shadow / live_read
    # evaluation metrics (§7)
    opportunities: int
    executable: int
    simulated: int
    simulated_success: int
    net_profitable_complete: int
    lost_to_competition: int | None
    expected_profit_per_attempt_usd: Decimal | None
    capital_efficiency: str | None           # net per USD of capital (per attempt or per 30 days for yield)
    worst_case_loss_usd: Decimal | None
    infra_cost_usd_month: Decimal
    repeatability: str
    sample_size: dict
    uncertainty: list[str]
    verdict: str                             # positive_attainable | positive_passive | negative | not_estimable | no_opportunity
    verdict_reason: str
    artefacts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d


def load_artefacts(docs_dir: str | Path) -> dict[tuple[str, str], dict[str, dict]]:
    """{(strategy, network): {mode: artefact}} from docs/phase5-<strategy>-<network>-<mode>.json."""
    out: dict[tuple[str, str], dict[str, dict]] = {}
    for p in sorted(glob.glob(str(Path(docs_dir) / "phase5-*-*-*.json"))):
        m = re.match(r"phase5-(?P<strategy>[a-z0-9_]+)-(?P<network>[a-z]+)-(?P<mode>discover|simulate|replay|shadow)\.json$", os.path.basename(p))
        if not m:
            continue
        with open(p) as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue
        out.setdefault((m["strategy"], m["network"]), {})[m["mode"]] = data
    return out


def _rows_of(art: dict | None) -> list[dict]:
    return list((art or {}).get("rows") or [])


def rank_one(strategy: str, network: str, arts: dict[str, dict], infra_cost: Decimal = INFRA_COST_USD_MONTH_DEFAULT) -> StrategyRow:
    disc, simu, rep, sh = arts.get("discover"), arts.get("simulate"), arts.get("replay"), arts.get("shadow")
    latest = simu or disc or {}
    rows = _rows_of(simu) or _rows_of(disc)
    opps = [r["opportunity"] for r in rows]
    sims = [r["simulation"] for r in rows if r.get("simulation")]
    comps = [r["competition"] for r in rows if r.get("competition")]
    kinds = {o.get("kind") for o in opps}
    kind = next(iter(kinds)) if len(kinds) == 1 else (",".join(sorted(k for k in kinds if k)) or "unknown")
    evidence: list[str] = []
    if rep:
        evidence.append("historical")
    if sims:
        evidence.append("fork")
    if sh:
        evidence.append("shadow")
    if opps:
        evidence.append("live_read")
    uncertainty: list[str] = []

    # capital / flash loan
    caps = [c for c in (_dec(o.get("required_capital_usd")) for o in opps) if c is not None]
    flash_flags = [o.get("flash_loan_eligible") for o in opps if o.get("flash_loan_eligible") is not None]
    flash = (any(flash_flags) if flash_flags else None)
    flash_note = next((o.get("flash_loan_note") for o in opps if o.get("flash_loan_note")), "")
    capital = _median(caps)
    capital_note = "own capital held (passive)" if kind == "yield" else ("flash-financed: own capital is the gas float only" if flash else "own capital per attempt")

    # gross / net: simulated figures win over theoretical ones, and are labeled as such
    gross_class, net_class = "theoretical", "theoretical"
    grosses = [g for g in (_dec(s.get("gross_profit_usd")) for s in sims) if g is not None]
    if grosses:
        gross_class = "simulated"
    else:
        grosses = [g for g in (_dec(o.get("gross_profit_usd")) for o in opps) if g is not None]
        gross_class = next((o.get("outcome_class") for o in opps if o.get("gross_profit_usd") is not None), "theoretical")
    gross = max(grosses) if grosses else None
    complete_sims = [s for s in sims if s.get("net_profit_complete") and s.get("net_profit_usd") is not None]
    if complete_sims:
        nets = [_dec(s["net_profit_usd"]) for s in complete_sims]
        net_class = "simulated"
    else:
        complete_opps = [o for o in opps if o.get("net_profit_complete") and o.get("net_profit_usd") is not None]
        nets = [_dec(o["net_profit_usd"]) for o in complete_opps]
        net_class = next((o.get("outcome_class") for o in complete_opps), "theoretical")
    net = max(nets) if nets else None
    if net is None:
        uncertainty.append("no opportunity with a complete net profit (every cost measured or the strategy carries none)")

    # competition
    ps = [p for p in (_dec(c.get("p_capture")) for c in comps) if p is not None]
    p = _median(ps) if ps else None
    model = next((c.get("model") for c in comps), None)
    assumptions = next((c.get("assumptions") for c in comps if c.get("assumptions")), [])
    atts = [a for a in (_dec(c.get("attainable_net_profit_usd")) for c in comps) if a is not None]
    attainable = max(atts) if atts else None
    if p is None:
        uncertainty.append("capture probability not estimated: " + (assumptions[0] if assumptions else "no competition model ran"))

    # frequency: replay window first (historical), then shadow
    freq_txt, freq_day = "not measured", None
    if rep:
        r = rep.get("replay") or rep
        fb, tb, n = r.get("from_block"), r.get("to_block"), r.get("opportunities")
        if fb is not None and tb is not None and n is not None and BLOCKS_PER_DAY.get(network):
            days = Decimal(tb - fb + 1) / Decimal(BLOCKS_PER_DAY[network])
            freq_day = quantize_usd(Decimal(n) / days) if days > 0 else None
            freq_txt = f"{n} in {days:.1f} days (blocks {fb:,}–{tb:,}; {r.get('sample_note', '')[:120]})"
    if sh:
        s_ = sh.get("shadow") or sh
        ticks, distinct, mins = s_.get("ticks"), s_.get("distinct_opportunities"), s_.get("minutes")
        txt = f"shadow: {distinct} distinct opportunities over {ticks} blocks in {mins:g} min"
        freq_txt = txt if freq_txt == "not measured" else f"{freq_txt}; {txt}"
        if freq_day is None and mins:
            freq_day = quantize_usd(Decimal(distinct) / (Decimal(str(mins)) / Decimal(1440)))
    if freq_txt == "not measured" and disc:
        freq_txt = f"{len(opps)} at the discovery block only (no replay or shadow run yet)"

    # risk: the worst among opportunities
    risks = [o.get("risk", "unknown") for o in opps]
    risk = max(risks, key=lambda x: RISK_ORDER.get(x, 3)) if risks else "unknown"

    # evaluation metrics
    executable = int((latest.get("summary") or {}).get("executable", 0)) if latest else 0
    sim_success = sum(1 for s in sims if s.get("success"))
    net_prof = sum(1 for s in complete_sims if _dec(s["net_profit_usd"]) > 0) if complete_sims else sum(1 for n_ in nets if n_ > 0)
    rr = (rep or {}).get("replay") or {}
    lost = rr.get("lost_to_competition")
    expected = _dec(rr.get("expected_profit_per_attempt_usd"))
    if expected is None and complete_sims:
        expected = quantize_usd(sum(nets, Decimal(0)) / len(nets))
    if expected is None and kind == "yield" and nets:
        expected = quantize_usd(sum(nets, Decimal(0)) / len(nets))
    worst = _dec(rr.get("worst_case_loss_usd"))
    if worst is None and sims:
        losses = [c for c in (_dec((s.get("costs") or {}).get("total_usd")) for s in sims if not s.get("success")) if c is not None]
        worst = max(losses) if losses else None
    cap_eff = None
    if net is not None and capital:
        per = "per 30 days" if kind == "yield" else "per attempt"
        cap_eff = f"{quantize_usd(net / capital * 100)}% of capital {per}"
    elif net is not None and capital == 0:
        cap_eff = "flash-financed: no own capital at risk beyond gas"
    if kind == "yield":
        repeat = "passive: repeats every day the rate holds; rates float with utilisation"
    elif rep and (rr.get("opportunities") or 0) > 0:
        repeat = f"{rr.get('simulated_success', 0)} of {rr.get('opportunities')} historical opportunities reproduced on the fork"
    elif sh:
        s_ = sh.get("shadow") or sh
        pers = s_.get("persistence_blocks") or {}
        repeat = f"shadow persistence: {len(pers)} opportunities, median lifetime {median(pers.values()) if pers else 0:g} blocks"
    else:
        repeat = "not measured"
    sample = {"opportunities": len(opps), "simulations": len(sims), "complete_simulations": len(complete_sims), "competition_rows": len(comps),
              "replay_opportunities": rr.get("opportunities"), "replay_simulated": rr.get("simulated"),
              "shadow_ticks": ((sh or {}).get("shadow") or {}).get("ticks")}
    if len(opps) < 10:
        uncertainty.append(f"small sample: {len(opps)} opportunities at the latest run")
    if rr and (rr.get("opportunities") or 0) < 10:
        uncertainty.append(f"small historical sample: {rr.get('opportunities', 0)} replay opportunities")

    # verdict
    if not opps and not rr:
        verdict, reason = "no_opportunity", "no opportunity discovered and no historical sample"
    elif kind == "yield":
        if net is not None and net > 0:
            verdict, reason = "positive_passive", f"passive yield {net} USD per 30 days on {capital} USD, net of entry/exit gas; not transaction revenue; risks listed"
        else:
            verdict, reason = "negative", "no positive passive net yield"
    elif attainable is not None:
        verdict = "positive_attainable" if attainable > 0 else "negative"
        reason = f"modeled attainable {attainable} USD (p = {p}) from {net_class} net profit {net} USD"
    elif net is not None:
        verdict = "negative" if net <= 0 else "not_estimable"
        reason = (f"{net_class} net profit {net} USD ≤ 0 at the best opportunity" if net <= 0
                  else f"{net_class} net profit {net} USD > 0 but capture probability not estimated")
    else:
        verdict, reason = ("negative", "every discovered spread or position is unprofitable before costs") if opps and gross is not None and gross <= 0 \
            else ("not_estimable", "no complete net profit figure")

    return StrategyRow(strategy, network, kind, (latest or rep or {}).get("description") or "", capital, capital_note, flash, flash_note, gross, gross_class,
                       net, net_class, p, model, list(assumptions or []), attainable, freq_txt, freq_day, risk, evidence,
                       len(opps), executable, len(sims), sim_success, net_prof, lost, expected, cap_eff, worst, infra_cost, repeat, sample, uncertainty,
                       verdict, reason, {m: {"generated_at": a.get("generated_at"), "block": a.get("block")} for m, a in arts.items()})


def rank(docs_dir: str | Path, infra_cost: Decimal | None = None) -> dict:
    infra = infra_cost if infra_cost is not None else _dec(os.environ.get("INFRA_COST_USD_MONTH")) or INFRA_COST_USD_MONTH_DEFAULT
    arts = load_artefacts(docs_dir)
    rows = [rank_one(s, n, a, infra) for (s, n), a in sorted(arts.items())]
    order = {"positive_attainable": 0, "positive_passive": 1, "not_estimable": 2, "negative": 3, "no_opportunity": 4}
    rows.sort(key=lambda r: (order.get(r.verdict, 9), -(r.attainable_profit_usd or r.net_profit_complete_usd or Decimal(-10**9))))
    qualifying = [r for r in rows if r.verdict == "positive_attainable"]
    passive = [r for r in rows if r.verdict == "positive_passive"]
    return {"generated_from": "docs/phase5-*.json (read-only artefacts)", "infra_cost_usd_month": str(infra),
            "rows": [r.as_dict() for r in rows],
            "conclusion": {"strategies_with_positive_attainable_net_profit": [f"{r.strategy}/{r.network}" for r in qualifying],
                           "strategies_with_positive_passive_yield": [f"{r.strategy}/{r.network}" for r in passive],
                           "statement": ("at least one strategy shows credible evidence of positive attainable net profit" if qualifying else
                                         "no transaction strategy qualifies on the evidence gathered; " + ("passive yield is positive but is not transaction revenue" if passive else "no passive yield qualifies either"))},
            "rules": ["simulated, theoretical, modeled and passive-yield figures are labeled and never summed together",
                      "complete net profit counts only simulations with every material cost measured",
                      "capture probability is reported only when a model with stated assumptions produced it"]}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=str(Path(__file__).resolve().parents[3] / "docs"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = rank(a.docs)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1)
    print(json.dumps({"rows": [(r["strategy"], r["network"], r["verdict"], r["net_profit_complete_usd"], r["attainable_profit_usd"]) for r in out["rows"]], "conclusion": out["conclusion"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
