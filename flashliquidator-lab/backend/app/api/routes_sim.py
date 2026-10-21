"""Simulation and backtest jobs. Writes require the administrator; everything runs on local forks only."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import BacktestRequest, JobAccepted, SimulationRequest
from app.core.config import Settings, get_settings
from app.core.security import rate_limit, require_admin
from app.database.base import get_db
from app.database import models as m

router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])


def _run_out(r: m.SimulationRun, res: m.SimulationResult | None) -> dict:
    out = {"id": r.id, "chain_id": r.chain_id, "kind": r.kind, "status": r.status.value, "outcome_class": r.outcome_class.value,
           "fork_block_number": r.fork_block_number, "fork_block_hash": r.fork_block_hash,
           "block_boundary_approximation": r.block_boundary_approximation, "input_params": r.input_params,
           "code_version": r.code_version, "rpc_capability": r.rpc_capability, "error": r.error,
           "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
           "historical_liquidation_id": r.historical_liquidation_id, "candidate_id": r.candidate_id, "result": None}
    if res:
        out["result"] = {"success": res.success, "revert_reason": res.revert_reason,
                         **{k: (None if getattr(res, k) is None else str(getattr(res, k))) for k in
                            ("debt_repaid_raw", "collateral_seized_raw", "swap_proceeds_raw", "flash_premium_raw", "gas_price_wei",
                             "net_profit_debt_raw", "net_profit_usd")},
                         "gas_used": res.gas_used, "trace_summary": res.trace_summary, "warnings": res.warnings,
                         "assumptions": res.assumptions, "calldata_hex": res.calldata_hex,
                         "calldata_note": "for local fork simulation only; this application cannot broadcast transactions"}
    return out


@router.post("/simulations", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_admin)])
def create_simulation(body: SimulationRequest, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    if body.flash_loan_provider != "aave_v3":
        raise HTTPException(422, "only aave_v3 flash loans are supported")
    nets = request.app.state.networks
    if body.network not in nets:
        raise HTTPException(404, f"unknown network '{body.network}'")
    run = m.SimulationRun(chain_id=nets[body.network].chain_id, kind="manual", status=m.JobStatus.queued,
                          fork_block_number=body.block_number, input_params=body.model_dump(mode="json"),
                          code_version=settings.app_version)
    db.add(run)
    db.commit()
    db.add(m.AuditEvent(actor="admin", action="simulation.create", target=str(run.id), detail={"network": body.network}))
    db.commit()
    from app.workers.tasks import simulate_run
    simulate_run.delay(run.id)
    return JobAccepted(job_id=run.id, status="queued", status_url=f"/api/simulations/{run.id}")


@router.get("/simulations")
def list_simulations(request: Request, db: Session = Depends(get_db), network: str | None = None, kind: str | None = None,
                     status: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    q = select(m.SimulationRun)
    if network:
        if network not in request.app.state.networks:
            raise HTTPException(404, f"unknown network '{network}'")
        q = q.where(m.SimulationRun.chain_id == request.app.state.networks[network].chain_id)
    if kind:
        q = q.where(m.SimulationRun.kind == kind)
    if status:
        q = q.where(m.SimulationRun.status == m.JobStatus(status))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    runs = db.scalars(q.order_by(m.SimulationRun.id.desc()).limit(limit).offset(offset)).all()
    results = {r.run_id: r for r in db.scalars(select(m.SimulationResult).where(m.SimulationResult.run_id.in_([r.id for r in runs])))}
    return {"items": [_run_out(r, results.get(r.id)) for r in runs], "total": total, "limit": limit, "offset": offset}


@router.get("/simulations/{run_id}")
def get_simulation(run_id: int, db: Session = Depends(get_db)):
    r = db.get(m.SimulationRun, run_id)
    if r is None:
        raise HTTPException(404, "simulation not found")
    res = db.scalar(select(m.SimulationResult).where(m.SimulationResult.run_id == run_id))
    return _run_out(r, res)


@router.post("/backtests", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_admin)])
def create_backtest(body: BacktestRequest, request: Request, db: Session = Depends(get_db)):
    nets = request.app.state.networks
    bad = [n for n in body.networks if n not in nets]
    if bad:
        raise HTTPException(404, f"unknown networks: {bad}")
    if body.date_to <= body.date_from:
        raise HTTPException(422, "date_to must be after date_from")
    sd = db.scalar(select(m.StrategyDefinition).where(m.StrategyDefinition.key == "replay"))
    if sd is None:
        sd = m.StrategyDefinition(key="replay", name="Historical replay", description="Fork replay of recorded liquidations", parameters={})
        db.add(sd)
        db.flush()
    run = m.StrategyRun(strategy_id=sd.id, kind="backtest", status=m.JobStatus.queued, outcome_class=m.OutcomeClass.simulated,
                        chain_ids=body.networks, date_from=body.date_from, date_to=body.date_to,
                        parameters={"strategies": body.strategies, "debt_assets": body.debt_assets, "collateral_assets": body.collateral_assets},
                        random_seed=body.random_seed)
    db.add(run)
    db.commit()
    from app.workers.tasks import run_backtest
    run_backtest.delay(run.id)
    return JobAccepted(job_id=run.id, status="queued", status_url=f"/api/backtests/{run.id}")


@router.get("/backtests/{run_id}")
def get_backtest(run_id: int, request: Request, db: Session = Depends(get_db), format: str = Query("json", pattern="^(json|csv)$")):
    r = db.get(m.StrategyRun, run_id)
    if r is None or r.kind != "backtest":
        raise HTTPException(404, "backtest not found")
    sims = db.execute(select(m.SimulationRun, m.SimulationResult).outerjoin(m.SimulationResult, m.SimulationResult.run_id == m.SimulationRun.id)
                      .where(m.SimulationRun.kind == "historical_replay",
                             m.SimulationRun.input_params["strategy_run_id"].as_integer() == run_id)
                      .order_by(m.SimulationRun.id)).all()
    rows = [_run_out(s, res) for s, res in sims]
    done = sum(1 for s, _ in sims if s.status in (m.JobStatus.succeeded, m.JobStatus.failed))
    body = {"id": r.id, "status": r.status.value, "outcome_class": r.outcome_class.value, "networks": r.chain_ids,
            "date_from": r.date_from, "date_to": r.date_to, "parameters": r.parameters, "random_seed": r.random_seed,
            "progress": (100 * done // len(sims)) if sims else r.progress, "sample_size": r.sample_size, "summary": r.summary,
            "assumptions": r.assumptions, "data_quality": r.data_quality, "error": r.error, "replays": rows}
    if format == "json":
        return body
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sim_id", "chain_id", "status", "historical_tx", "fork_block", "success", "revert_reason", "debt_repaid_raw",
                "collateral_seized_raw", "swap_proceeds_raw", "flash_premium_raw", "gas_used", "net_profit_usd", "block_boundary_approximation"])
    for x in rows:
        res = x["result"] or {}
        w.writerow([x["id"], x["chain_id"], x["status"], x["input_params"].get("historical_tx"), x["fork_block_number"], res.get("success"),
                    res.get("revert_reason"), res.get("debt_repaid_raw"), res.get("collateral_seized_raw"), res.get("swap_proceeds_raw"),
                    res.get("flash_premium_raw"), res.get("gas_used"), res.get("net_profit_usd"), x["block_boundary_approximation"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=backtest-{run_id}.csv"})
