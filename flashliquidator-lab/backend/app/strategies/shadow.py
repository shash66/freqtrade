"""Live shadow mode (spec §10): read-only. Tracks what happened to each candidate we identified: liquidated by
someone else (competitor observation), removed (position healed/repaid), or still open. Latency is measured
with a monotonic clock between block observation and candidate creation; block timestamps are never used
as latency. Nothing here can submit a transaction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chains.config import NetworkConfig
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.money import WAD
from app.database.base import get_sessionmaker
from app.database import models as m

log = get_logger(__name__)

OPPORTUNITIES = Counter("fll_shadow_opportunities_total", "candidates identified in shadow mode", ["network"])
COMPETITOR_TAKEN = Counter("fll_shadow_competitor_liquidated_total", "candidates liquidated on-chain by others", ["network"])
EXPIRED = Counter("fll_shadow_expired_total", "candidates that disappeared without liquidation", ["network"])
SIM_QUEUED = Counter("fll_shadow_simulations_queued_total", "candidate simulations queued", ["network"])
OPEN = Gauge("fll_shadow_open_candidates", "candidates still open", ["network"])
LATENCY = Histogram("fll_detection_to_candidate_seconds", "block observation -> candidate stored", ["network"],
                    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30))


def track_outcomes(net: NetworkConfig, db: Session, lookback_hours: int = 24) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    open_c = db.scalars(select(m.LiquidationCandidate).where(m.LiquidationCandidate.chain_id == net.chain_id,
                                                             m.LiquidationCandidate.status.in_([m.CandidateStatus.candidate, m.CandidateStatus.simulated]),
                                                             m.LiquidationCandidate.created_at >= since)).all()
    out = {"open": len(open_c), "taken_by_competitor": 0, "removed": 0}
    for c in open_c:
        b = db.get(m.Borrower, c.borrower_id)
        liq = db.scalar(select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id == net.chain_id,
                                                              func.lower(m.HistoricalLiquidation.borrower) == b.address.lower(),
                                                              m.HistoricalLiquidation.block_number >= c.block_number)
                        .order_by(m.HistoricalLiquidation.block_number.asc()))
        if liq is not None:
            exists = db.scalar(select(m.CompetitorObservation).where(m.CompetitorObservation.candidate_id == c.id))
            if exists is None:
                db.add(m.CompetitorObservation(chain_id=net.chain_id, liquidator=liq.liquidator, historical_liquidation_id=liq.id,
                                               candidate_id=c.id, eligibility_block=c.block_number, liquidation_block=liq.block_number,
                                               blocks_to_liquidation=liq.block_number - c.block_number, gas_used=liq.gas_used,
                                               priority_fee_wei=liq.effective_gas_price_wei,
                                               notes="eligibility block = first block this system saw HF<1 for this candidate; "
                                                     "a lower bound on competitor reaction time"))
            c.status = m.CandidateStatus.expired
            c.incomplete_reasons = (c.incomplete_reasons or []) + [f"liquidated on-chain by {liq.liquidator} in block {liq.block_number}"]
            out["taken_by_competitor"] += 1
            COMPETITOR_TAKEN.labels(net.slug).inc()
        elif b.health_factor_wad is not None and b.health_factor_wad >= WAD and (b.last_reconciled_block or 0) > c.block_number:
            c.status = m.CandidateStatus.expired
            c.incomplete_reasons = (c.incomplete_reasons or []) + ["position no longer liquidatable (healed/repaid) without a liquidation"]
            out["removed"] += 1
            EXPIRED.labels(net.slug).inc()
    db.commit()
    OPEN.labels(net.slug).set(out["open"] - out["taken_by_competitor"] - out["removed"])
    return out


def queue_candidate_simulations(net: NetworkConfig, db: Session, max_new: int) -> int:
    """Queue fork simulations for the most profitable fresh candidates that have none yet (bounded)."""
    from app.workers.tasks import simulate_run
    s = get_settings()
    cands = db.scalars(select(m.LiquidationCandidate).where(m.LiquidationCandidate.chain_id == net.chain_id,
                                                            m.LiquidationCandidate.status == m.CandidateStatus.candidate)
                       .order_by(m.LiquidationCandidate.net_profit_usd.desc().nulls_last()).limit(max_new * 3)).all()
    n = 0
    for c in cands:
        if n >= max_new:
            break
        if db.scalar(select(m.SimulationRun).where(m.SimulationRun.candidate_id == c.id)) is not None:
            continue
        b = db.get(m.Borrower, c.borrower_id)
        da, ca = db.get(m.Asset, c.debt_asset_id), db.get(m.Asset, c.collateral_asset_id)
        run = m.SimulationRun(chain_id=net.chain_id, kind="candidate", status=m.JobStatus.queued, candidate_id=c.id,
                              input_params={"borrower": b.address, "collateral_asset": ca.address, "debt_asset": da.address,
                                            "debt_to_cover_raw": str(c.debt_to_cover_raw), "block_number": None,
                                            "gas_price_wei": None if c.gas_price_wei is None else str(c.gas_price_wei)},
                              code_version=s.app_version, pool_address=net.market.pool, pool_impl_address=net.market.pool_impl)
        db.add(run)
        db.flush()
        c.status = m.CandidateStatus.simulated
        simulate_run.delay(run.id)
        SIM_QUEUED.labels(net.slug).inc()
        n += 1
    db.commit()
    return n


def shadow_tick(net: NetworkConfig) -> dict:
    s = get_settings()
    with get_sessionmaker()() as db:
        outcomes = track_outcomes(net, db)
        queued = queue_candidate_simulations(net, db, max_new=s.simulation_concurrency)
    return {**outcomes, "simulations_queued": queued}


def shadow_summary(db: Session, chains: dict[str, int]) -> dict:
    out = {"outcome_classes": {"candidates": "theoretical", "simulations": "simulated", "competitor_liquidations": "onchain_actual"},
           "networks": {}}
    for slug, cid in chains.items():
        cnt = lambda *w: db.scalar(select(func.count()).select_from(m.LiquidationCandidate).where(m.LiquidationCandidate.chain_id == cid, *w)) or 0
        sims = db.execute(select(m.SimulationRun.status, func.count()).where(m.SimulationRun.chain_id == cid, m.SimulationRun.kind == "candidate")
                          .group_by(m.SimulationRun.status)).all()
        ok = db.scalar(select(func.count()).select_from(m.SimulationResult).join(m.SimulationRun, m.SimulationRun.id == m.SimulationResult.run_id)
                       .where(m.SimulationRun.chain_id == cid, m.SimulationRun.kind == "candidate", m.SimulationResult.success.is_(True))) or 0
        prof = db.scalar(select(func.count()).select_from(m.SimulationResult).join(m.SimulationRun, m.SimulationRun.id == m.SimulationResult.run_id)
                         .where(m.SimulationRun.chain_id == cid, m.SimulationRun.kind == "candidate", m.SimulationResult.net_profit_usd > 0)) or 0
        rev = db.scalar(select(func.count()).select_from(m.SimulationResult).join(m.SimulationRun, m.SimulationRun.id == m.SimulationResult.run_id)
                        .where(m.SimulationRun.chain_id == cid, m.SimulationRun.kind == "candidate", m.SimulationResult.success.is_(False))) or 0
        taken = db.scalar(select(func.count()).select_from(m.CompetitorObservation).where(m.CompetitorObservation.chain_id == cid,
                                                                                         m.CompetitorObservation.candidate_id.is_not(None))) or 0
        rpc_err = db.scalar(select(func.count()).select_from(m.RpcHealthMetric).where(m.RpcHealthMetric.chain_id == cid, m.RpcHealthMetric.ok.is_(False),
                                                                                    m.RpcHealthMetric.observed_at >= datetime.now(timezone.utc) - timedelta(hours=24))) or 0
        out["networks"][slug] = {
            "opportunities_discovered": cnt(), "candidates_complete": cnt(m.LiquidationCandidate.status.in_([m.CandidateStatus.candidate, m.CandidateStatus.simulated])),
            "candidates_incomplete": cnt(m.LiquidationCandidate.status == m.CandidateStatus.incomplete),
            "candidates_invalid": cnt(m.LiquidationCandidate.status == m.CandidateStatus.invalid),
            "simulations_by_status": {st.value: n for st, n in sims}, "valid_simulations": ok, "simulated_reverts": rev,
            "profitable_simulations": prof, "liquidated_by_competitors": taken, "rpc_errors_24h": rpc_err,
            "note": "simulated profit is not earned revenue; capturability is modeled in /api/strategies/compare",
        }
    return out
