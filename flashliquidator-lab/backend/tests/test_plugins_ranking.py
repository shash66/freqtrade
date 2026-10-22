"""Unified ranking (Phase 5 §6–7): labels, never-mix rules, verdicts, frequency and sample sizes from artefacts."""
from __future__ import annotations

import json
from decimal import Decimal

from app.plugins.ranking import load_artefacts, rank, rank_one


def _opp(**kw):
    base = {"strategy": "s", "network": "base", "kind": "arbitrage", "key": "k", "block": 1, "description": "d", "required_capital_usd": "0",
            "flash_loan_eligible": True, "flash_loan_note": "flash", "gross_profit_usd": None, "costs": {"total_usd": None}, "net_profit_usd": None,
            "net_profit_complete": False, "outcome_class": "theoretical", "risk": "medium", "state": {"kind": "arbitrage", "executable": True}}
    base.update(kw)
    return base


def _sim(success, gross, net, complete, total="0.30"):
    return {"success": success, "outcome_class": "simulated", "revert_reason": None if success else "InsufficientProfit", "gross_profit_usd": gross,
            "costs": {"total_usd": total, "missing": []}, "net_profit_usd": net, "net_profit_complete": complete}


def test_simulated_figures_win_and_are_labeled_and_attainable_needs_a_model():
    arts = {"simulate": {"summary": {"executable": 1}, "rows": [
        {"opportunity": _opp(gross_profit_usd="5.00"), "simulation": _sim(True, "4.00", "3.50", True),
         "competition": {"p_capture": "0.4", "attainable_net_profit_usd": "1.28", "failed_attempt_cost_usd": "0.18", "model": "m", "assumptions": ["a"]}}]}}
    r = rank_one("dex_arb", "base", arts)
    assert r.gross_profit_usd == Decimal("4.00") and r.gross_class == "simulated"
    assert r.net_profit_complete_usd == Decimal("3.50") and r.net_class == "simulated"
    assert r.p_capture == Decimal("0.4") and r.attainable_profit_usd == Decimal("1.28")
    assert r.verdict == "positive_attainable" and "fork" in r.evidence_quality and "historical" not in r.evidence_quality


def test_incomplete_simulation_never_counts_as_net_profit():
    arts = {"simulate": {"summary": {"executable": 1}, "rows": [
        {"opportunity": _opp(), "simulation": _sim(True, "4.00", None, False, None), "competition": {"p_capture": None, "attainable_net_profit_usd": None, "model": "m", "assumptions": ["no complete net"]}}]}}
    r = rank_one("dex_arb", "base", arts)
    assert r.net_profit_complete_usd is None and r.net_profitable_complete == 0 and r.verdict == "not_estimable"
    assert any("complete" in u for u in r.uncertainty)


def test_negative_spreads_are_negative_not_zero():
    arts = {"discover": {"summary": {"executable": 0}, "rows": [{"opportunity": _opp(gross_profit_usd="-0.04", state={"kind": "arbitrage", "executable": False}),
                                                               "competition": {"p_capture": None, "attainable_net_profit_usd": None, "model": "m", "assumptions": []}}]}}
    r = rank_one("dex_arb", "base", arts)
    assert r.verdict == "negative" and r.gross_profit_usd == Decimal("-0.04") and r.gross_class == "theoretical"


def test_passive_yield_is_its_own_verdict_and_never_positive_attainable():
    arts = {"discover": {"summary": {"executable": 1}, "rows": [{"opportunity": _opp(kind="yield", required_capital_usd="10000", flash_loan_eligible=False, gross_profit_usd="45",
                                                                      net_profit_usd="44.4", net_profit_complete=True, outcome_class="passive_yield", risk="low",
                                                                      state={"kind": "stable_lending"}),
                                                               "competition": {"p_capture": "1", "attainable_net_profit_usd": "44.4", "model": "none_passive", "assumptions": ["no competition"]}}]}}
    r = rank_one("yield_opt", "base", arts)
    assert r.verdict == "positive_passive" and r.net_class == "passive_yield" and r.capital_usd == Decimal("10000")
    assert r.capital_efficiency.endswith("per 30 days")


def test_frequency_from_replay_window_and_shadow(tmp_path):
    rep = {"strategy": "compound_v3", "network": "base", "mode": "replay", "replay": {"from_block": 1, "to_block": 43_200 * 2, "opportunities": 4, "simulated": 4,
           "simulated_success": 1, "net_profitable_complete": 0, "lost_to_competition": 3, "expected_profit_per_attempt_usd": "-0.20", "worst_case_loss_usd": "0.90", "sample_note": "n"}}
    sh = {"strategy": "compound_v3", "network": "base", "mode": "shadow", "shadow": {"minutes": 30, "ticks": 800, "distinct_opportunities": 0, "persistence_blocks": {}}}
    (tmp_path / "phase5-compound_v3-base-replay.json").write_text(json.dumps(rep))
    (tmp_path / "phase5-compound_v3-base-shadow.json").write_text(json.dumps(sh))
    (tmp_path / "phase5-compound_v3-base-discover.json").write_text(json.dumps({"summary": {"executable": 0}, "rows": []}))
    arts = load_artefacts(tmp_path)
    assert set(arts) == {("compound_v3", "base")} and set(arts[("compound_v3", "base")]) == {"replay", "shadow", "discover"}
    out = rank(tmp_path, infra_cost=Decimal("69"))
    row = out["rows"][0]
    assert row["frequency_per_day"] == "2.000000" and "2.0 days" in row["frequency_observed"] and "shadow" in row["frequency_observed"]
    assert row["lost_to_competition"] == 3 and row["worst_case_loss_usd"] == "0.90" and row["expected_profit_per_attempt_usd"] == "-0.20"
    assert row["evidence_quality"] == ["historical", "shadow"] and row["infra_cost_usd_month"] == "69"
    assert out["conclusion"]["strategies_with_positive_attainable_net_profit"] == []
    assert "no transaction strategy qualifies" in out["conclusion"]["statement"]


def test_rank_orders_positive_first_and_ignores_unrelated_files(tmp_path):
    (tmp_path / "phase5-comet-history-base.json").write_text("{}")   # not a <strategy>-<network>-<mode> artefact
    (tmp_path / "phase5-dex_arb-base-discover.json").write_text(json.dumps({"summary": {"executable": 0}, "rows": [
        {"opportunity": _opp(gross_profit_usd="-0.1", state={"kind": "arbitrage", "executable": False}), "competition": {"p_capture": None, "model": "m", "assumptions": []}}]}))
    (tmp_path / "phase5-yield_opt-base-discover.json").write_text(json.dumps({"summary": {"executable": 1}, "rows": [
        {"opportunity": _opp(kind="yield", required_capital_usd="10000", net_profit_usd="10", net_profit_complete=True, outcome_class="passive_yield", state={"kind": "stable_lending"}),
         "competition": {"p_capture": "1", "attainable_net_profit_usd": "10", "model": "none_passive", "assumptions": []}}]}))
    out = rank(tmp_path)
    assert [r["strategy"] for r in out["rows"]] == ["yield_opt", "dex_arb"]
    assert out["conclusion"]["strategies_with_positive_passive_yield"] == ["yield_opt/base"]


def test_dust_only_compound_rows_are_negative_not_estimable():
    arts = {"simulate": {"summary": {"executable": 0}, "rows": [
        {"opportunity": _opp(kind="liquidation", gross_profit_usd="0.0009", state={"kind": "reserve_purchase", "executable": False}),
         "simulation": {"success": False, "outcome_class": "simulated", "revert_reason": "dust: ...", "gross_profit_usd": "0.0009", "costs": {"total_usd": None, "missing": ["not attempted"]},
                        "net_profit_usd": None, "net_profit_complete": False},
         "competition": {"p_capture": None, "attainable_net_profit_usd": None, "model": "m", "assumptions": ["no bids"]}}]}}
    r = rank_one("compound_v3", "base", arts)
    assert r.verdict == "negative" and "dust" in r.verdict_reason and r.net_profit_complete_usd is None
