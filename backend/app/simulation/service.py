"""Persist simulation runs/results and drive historical replay batches."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chains.config import NetworkConfig
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database import models as m
from app.database.base import get_sessionmaker
from app.simulation.replay import ReplayRequest, ReplayResult, run_replay

log = get_logger(__name__)


def _store_result(db: Session, run: m.SimulationRun, r: ReplayResult) -> None:
    run.status = m.JobStatus.succeeded if r.success else m.JobStatus.failed
    run.fork_block_number, run.fork_block_hash = r.fork_block, r.fork_block_hash
    run.block_boundary_approximation = r.block_boundary_approximation
    run.rpc_capability = r.rpc_capability
    run.error = None if r.success else r.revert_reason
    run.finished_at = datetime.now(UTC)
    db.add(m.SimulationResult(run_id=run.id, success=r.success, revert_reason=r.revert_reason, debt_repaid_raw=r.debt_repaid_raw,
                              collateral_seized_raw=r.collateral_seized_raw, swap_proceeds_raw=r.swap_proceeds_raw,
                              flash_premium_raw=r.flash_premium_raw, gas_used=r.gas_used, gas_price_wei=r.gas_price_wei,
                              net_profit_debt_raw=r.net_profit_debt_raw, net_profit_usd=r.net_profit_usd,
                              trace_summary={"calls": r.trace_summary, "health_factor_at_fork": r.health_factor_at_fork,
                                             "eligible_at_fork": r.eligible_at_fork, "funding_path": r.funding_path},
                              calldata_hex=r.calldata_hex, warnings=r.warnings, assumptions=r.assumptions,
                              fees=r.fees, execution_path=r.execution_path))


def execute_simulation_run(run_id: int, networks: dict[str, NetworkConfig]) -> dict:
    get_settings()
    with get_sessionmaker()() as db:
        run = db.get(m.SimulationRun, run_id)
        if run is None:
            return {"error": "run not found"}
        net = next((n for n in networks.values() if n.chain_id == run.chain_id), None)
        if net is None:
            run.status, run.error = m.JobStatus.failed, "network disabled"
            db.commit()
            return {"error": "network disabled"}
        run.status, run.started_at = m.JobStatus.running, datetime.now(UTC)
        db.commit()
        p = run.input_params
        req = ReplayRequest(network=net, borrower=p["borrower"], collateral_asset=p["collateral_asset"], debt_asset=p["debt_asset"],
                            debt_to_cover_raw=int(p["debt_to_cover_raw"]), block_number=p.get("block_number"),
                            gas_price_wei=int(p["gas_price_wei"]) if p.get("gas_price_wei") else None,
                            slippage_bps=int(p.get("slippage_bps", 50)), historical_tx=p.get("historical_tx"),
                            force_direct_path=bool(p.get("force_direct_path", False)))
        try:
            r = run_replay(req)
        except Exception as e:  # any failure is recorded on the run; never silently dropped
            run.status, run.error, run.finished_at = m.JobStatus.failed, f"{type(e).__name__}: {str(e)[:400]}", datetime.now(UTC)
            db.commit()
            log.error("simulation_crashed", run_id=run_id, error=run.error)
            return {"error": run.error}
        _store_result(db, run, r)
        db.commit()
        return {"success": r.success, "net_profit_usd": None if r.net_profit_usd is None else str(r.net_profit_usd)}


def create_replay_runs_for_backtest(db: Session, strategy_run: m.StrategyRun, networks: dict[str, NetworkConfig], max_events: int = 500) -> list[int]:
    """One historical_replay SimulationRun per historical liquidation in the backtest window (bounded)."""
    chain_ids = [networks[s].chain_id for s in strategy_run.chain_ids if s in networks]
    q = select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id.in_(chain_ids),
                                              (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0))
    if strategy_run.date_from:
        q = q.where(m.HistoricalLiquidation.block_timestamp >= strategy_run.date_from)
    if strategy_run.date_to:
        q = q.where(m.HistoricalLiquidation.block_timestamp <= strategy_run.date_to)
    params = strategy_run.parameters or {}
    if params.get("debt_assets"):
        q = q.where(m.HistoricalLiquidation.debt_asset.in_(params["debt_assets"]))
    if params.get("collateral_assets"):
        q = q.where(m.HistoricalLiquidation.collateral_asset.in_(params["collateral_assets"]))
    events = db.scalars(q.order_by(m.HistoricalLiquidation.block_number.desc()).limit(max_events)).all()
    ids = []
    for h in events:
        run = m.SimulationRun(chain_id=h.chain_id, kind="historical_replay", status=m.JobStatus.queued, fork_block_number=h.block_number - 1,
                              historical_liquidation_id=h.id, pool_address=None, pool_impl_address=h.pool_impl_address,
                              input_params={"borrower": h.borrower, "collateral_asset": h.collateral_asset, "debt_asset": h.debt_asset,
                                            "debt_to_cover_raw": str(h.debt_repaid_raw), "block_number": h.block_number,
                                            "gas_price_wei": None if h.effective_gas_price_wei is None else str(h.effective_gas_price_wei),
                                            "historical_tx": h.tx_hash, "strategy_run_id": strategy_run.id},
                              code_version=get_settings().app_version, strategy_key=None)
        db.add(run)
        db.flush()
        ids.append(run.id)
    strategy_run.sample_size = len(ids)
    db.commit()
    return ids
