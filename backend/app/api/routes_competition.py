from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.competition.analysis import competitor_summary
from app.competition.montecarlo import delay_sensitivity, simulate
from app.competition.strategies import COMPETITOR_DEFAULTS, STRATEGIES
from app.core.security import rate_limit
from app.database.base import get_db
from app.database import models as m
from app.strategies.shadow import shadow_summary

router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])


class CompareRequest(BaseModel):
    strategies: list[str] = ["A", "B", "C"]
    seed: int = 42
    trials: int = Field(5000, ge=100, le=100_000)
    competition: dict = {}
    strategy_overrides: dict[str, dict] = {}
    expected_profit_usd: float | None = None   # default: median simulated net profit from replays, if any
    include_sensitivity: bool = True


@router.get("/strategies")
def strategies():
    return [{"key": k, **v} for k, v in STRATEGIES.items()]


@router.post("/strategies/compare")
def compare(body: CompareRequest, db: Session = Depends(get_db)):
    bad = [k for k in body.strategies if k not in STRATEGIES]
    if bad:
        raise HTTPException(422, f"unknown strategies: {bad}")
    comp = dict(COMPETITOR_DEFAULTS, **body.competition, trials=body.trials)
    source = "request" if body.expected_profit_usd is not None else "default assumption"
    if body.expected_profit_usd is not None:
        comp["expected_profit_usd"] = body.expected_profit_usd
    else:
        profits = [float(r.net_profit_usd) for r in db.scalars(select(m.SimulationResult).where(m.SimulationResult.success.is_(True),
                                                                                                m.SimulationResult.net_profit_usd.is_not(None)))]
        if profits:
            comp["expected_profit_usd"] = sorted(profits)[len(profits) // 2]
            source = f"median of {len(profits)} simulated replays"
    results = {}
    for k in body.strategies:
        params = dict(STRATEGIES[k]["parameters"], **body.strategy_overrides.get(k, {}))
        r = simulate(params, comp, body.seed)
        results[k] = {"name": STRATEGIES[k]["name"], "parameters": params, **r.__dict__}
        if body.include_sensitivity:
            results[k]["delay_sensitivity"] = delay_sensitivity(params, comp, body.seed)
    return {"outcome_class": "modeled_capturable", "label": "modeled estimates under stated assumptions; not recorded performance",
            "seed": body.seed, "competition_assumptions": comp, "expected_profit_source": source, "results": results}


@router.get("/competition/summary")
def competition(request: Request, db: Session = Depends(get_db), days: int = 30):
    chains = {s: n.chain_id for s, n in request.app.state.networks.items()}
    return {"competitors": competitor_summary(db, chains, days), "shadow": shadow_summary(db, chains)}
