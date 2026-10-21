"""Summaries over replay datasets. Every figure is labeled by outcome class; nothing is pooled across classes."""
from __future__ import annotations

from decimal import Decimal
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models as m


def summarize_backtest(db: Session, sr: m.StrategyRun) -> tuple[dict, list[str], dict]:
    rows = db.execute(select(m.SimulationRun, m.SimulationResult)
                      .outerjoin(m.SimulationResult, m.SimulationResult.run_id == m.SimulationRun.id)
                      .where(m.SimulationRun.kind == "historical_replay",
                             m.SimulationRun.input_params["strategy_run_id"].as_integer() == sr.id)).all()
    n = len(rows)
    ok = [res for run, res in rows if res is not None and res.success]
    reverted = [res for run, res in rows if res is not None and not res.success]
    skipped = [run for run, res in rows if res is None]
    profits = [res.net_profit_usd for res in ok if res.net_profit_usd is not None]
    profitable = [p for p in profits if p > 0]
    not_eligible_at_boundary = sum(1 for run, res in rows if res is not None and res.trace_summary
                                   and res.trace_summary.get("eligible_at_fork") is False)
    summary = {
        "outcome_class": "simulated",
        "sample_size": n, "simulated_ok": len(ok), "simulated_reverts": len(reverted), "skipped": len(skipped),
        "not_eligible_at_block_boundary": not_eligible_at_boundary,
        "with_complete_profit_figure": len(profits), "profitable_simulations": len(profitable),
        "net_profit_usd_total": str(sum(profits, Decimal(0))) if profits else None,
        "net_profit_usd_median": str(median(profits)) if profits else None,
        "net_profit_usd_p10": str(sorted(profits)[max(0, len(profits) // 10)]) if profits else None,
        "net_profit_usd_p90": str(sorted(profits)[min(len(profits) - 1, (9 * len(profits)) // 10)]) if profits else None,
    }
    assumptions = [
        "replays fork at N-1 (block-boundary approximation); intra-block ordering not reproduced",
        "swap proceeds quoted, not executed; flash premium added analytically",
        "a profitable simulation is not a capturable opportunity: competition is modeled separately (Competition Lab)",
    ]
    quality = {"archive_rpc_used": any((run.rpc_capability or {}).get("archive_rpc") for run, _ in rows),
               "events_requested": sr.sample_size, "events_replayed": n - len(skipped)}
    return summary, assumptions, quality
