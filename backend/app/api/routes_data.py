"""Read endpoints over indexed data: positions, liquidation history, opportunities."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import BorrowerOut, OpportunityOut, Page
from app.core.money import to_units
from app.core.security import rate_limit
from app.database.base import get_db
from app.database import models as m
from app.indexer.reconcile import hf_str

router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])


def _chain(request: Request, network: str | None) -> dict[str, int]:
    nets = request.app.state.networks
    if network is None:
        return {s: n.chain_id for s, n in nets.items()}
    if network not in nets:
        raise HTTPException(404, f"unknown network '{network}'")
    return {network: nets[network].chain_id}


def _slug(request: Request, chain_id: int) -> str:
    return next((s for s, n in request.app.state.networks.items() if n.chain_id == chain_id), str(chain_id))


def _usd(base: int | None) -> str | None:
    return None if base is None else str(to_units(int(base), 8))


@router.get("/positions", response_model=Page[BorrowerOut])
def positions(request: Request, db: Session = Depends(get_db), network: str | None = None,
              max_health_factor: Decimal | None = Query(None, gt=0), min_debt_usd: Decimal | None = Query(None, ge=0),
              sort: str = Query("priority", pattern="^(priority|health_factor|debt)$"),
              limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    chains = list(_chain(request, network).values())
    q = select(m.Borrower).where(m.Borrower.chain_id.in_(chains), m.Borrower.is_active.is_(True))
    if max_health_factor is not None:
        q = q.where(m.Borrower.health_factor_wad < int(max_health_factor * 10**18))
    if min_debt_usd is not None:
        q = q.where(m.Borrower.total_debt_base >= int(min_debt_usd * 10**8))
    order = {"priority": m.Borrower.priority_score.desc().nulls_last(), "health_factor": m.Borrower.health_factor_wad.asc().nulls_last(),
             "debt": m.Borrower.total_debt_base.desc().nulls_last()}[sort]
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(order).limit(limit).offset(offset)).all()
    items = [BorrowerOut(network=_slug(request, b.chain_id), address=b.address, health_factor=hf_str(b.health_factor_wad),
                         total_collateral_usd=_usd(b.total_collateral_base), total_debt_usd=_usd(b.total_debt_base),
                         current_liquidation_threshold_bps=b.current_liquidation_threshold_bps, emode_category=b.emode_category,
                         last_reconciled_block=b.last_reconciled_block, last_reconciled_at=b.last_reconciled_at,
                         priority_score=None if b.priority_score is None else str(b.priority_score)) for b in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/positions/{network}/{address}")
def position_detail(network: str, address: str, request: Request, db: Session = Depends(get_db)):
    cid = _chain(request, network)[network]
    b = db.scalar(select(m.Borrower).where(m.Borrower.chain_id == cid, func.lower(m.Borrower.address) == address.lower()))
    if b is None:
        raise HTTPException(404, "borrower not indexed")
    reserves = db.execute(select(m.BorrowerReserveState, m.Asset).join(m.Asset, m.Asset.id == m.BorrowerReserveState.asset_id)
                          .where(m.BorrowerReserveState.borrower_id == b.id)).all()
    snaps = db.scalars(select(m.PositionSnapshot).where(m.PositionSnapshot.borrower_id == b.id)
                       .order_by(m.PositionSnapshot.block_number.desc()).limit(50)).all()
    return {
        "network": network, "address": b.address, "health_factor": hf_str(b.health_factor_wad),
        "total_collateral_usd": _usd(b.total_collateral_base), "total_debt_usd": _usd(b.total_debt_base),
        "current_liquidation_threshold_bps": b.current_liquidation_threshold_bps, "ltv_bps": b.ltv_bps,
        "emode_category": b.emode_category, "last_reconciled_block": b.last_reconciled_block,
        "last_reconciled_at": b.last_reconciled_at, "first_seen_block": b.first_seen_block, "last_event_block": b.last_event_block,
        "reserves": [{"asset": a.address, "symbol": a.symbol, "decimals": a.decimals, "a_token_balance": str(r.a_token_balance),
                      "variable_debt": str(r.variable_debt), "stable_debt": str(r.stable_debt), "collateral": r.usage_as_collateral,
                      "liquidation_threshold_bps": a.liquidation_threshold_bps, "liquidation_bonus_bps": a.liquidation_bonus_bps,
                      "liquidation_protocol_fee_bps": a.liquidation_protocol_fee_bps, "observed_block": r.observed_block}
                     for r, a in reserves],
        "snapshots": [{"block_number": sn.block_number, "health_factor": hf_str(sn.health_factor_wad),
                       "total_debt_usd": _usd(sn.total_debt_base), "observed_at": sn.observed_at} for sn in snaps],
    }


@router.get("/liquidations/history")
def liquidation_history(request: Request, db: Session = Depends(get_db), network: str | None = None,
                        date_from: datetime | None = None, date_to: datetime | None = None,
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    chains = list(_chain(request, network).values())
    q = select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id.in_(chains),
                                              (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0))
    if date_from:
        q = q.where(m.HistoricalLiquidation.block_timestamp >= date_from)
    if date_to:
        q = q.where(m.HistoricalLiquidation.block_timestamp <= date_to)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(m.HistoricalLiquidation.block_number.desc()).limit(limit).offset(offset)).all()
    assets = {(a.chain_id, a.address.lower()): a for a in db.scalars(select(m.Asset).where(m.Asset.chain_id.in_(chains)))}

    def fmt(cid, addr, raw):
        a = assets.get((cid, addr.lower()))
        return {"asset": addr, "symbol": a.symbol if a else None, "raw": str(raw),
                "amount": str(to_units(int(raw), a.decimals)) if a else None}

    items = [{"network": _slug(request, r.chain_id), "tx_hash": r.tx_hash, "log_index": r.log_index, "block_number": r.block_number,
              "block_hash": r.block_hash, "block_timestamp": r.block_timestamp, "tx_index": r.tx_index, "borrower": r.borrower,
              "liquidator": r.liquidator, "debt": fmt(r.chain_id, r.debt_asset, r.debt_repaid_raw),
              "collateral": fmt(r.chain_id, r.collateral_asset, r.collateral_seized_raw), "receive_a_token": r.receive_a_token,
              "gas_used": r.gas_used, "effective_gas_price_wei": None if r.effective_gas_price_wei is None else str(r.effective_gas_price_wei),
              "tx_status": r.tx_status, "pool_impl_address": r.pool_impl_address,
              "explorer_url": f"{request.app.state.networks[_slug(request, r.chain_id)].explorer_url}/tx/{r.tx_hash}"
              if _slug(request, r.chain_id) in request.app.state.networks else None} for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "note": "Source: Aave V3 Pool LiquidationCall events read from the configured RPC; gas fields come from receipts."}


@router.get("/opportunities", response_model=Page[OpportunityOut])
def opportunities(request: Request, db: Session = Depends(get_db), network: str | None = None,
                  status: str | None = Query(None, pattern="^(candidate|simulated|invalid|expired|incomplete)$"),
                  min_profit_usd: Decimal | None = None, sort: str = Query("net_profit", pattern="^(net_profit|created|health_factor)$"),
                  limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    chains = list(_chain(request, network).values())
    q = select(m.LiquidationCandidate).where(m.LiquidationCandidate.chain_id.in_(chains))
    if status:
        q = q.where(m.LiquidationCandidate.status == m.CandidateStatus(status))
    if min_profit_usd is not None:
        q = q.where(m.LiquidationCandidate.net_profit_usd >= min_profit_usd)
    order = {"net_profit": m.LiquidationCandidate.net_profit_usd.desc().nulls_last(),
             "created": m.LiquidationCandidate.created_at.desc(),
             "health_factor": m.LiquidationCandidate.health_factor_wad.asc()}[sort]
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(order).limit(limit).offset(offset)).all()
    items = [_opportunity_out(request, db, c) for c in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(m.LiquidationCandidate, opportunity_id)
    if c is None:
        raise HTTPException(404, "opportunity not found")
    out = _opportunity_out(request, db, c).model_dump()
    out.update({"breakdown": c.breakdown, "data_sources": c.data_sources, "block_hash": c.block_hash,
                "close_factor_bps": c.close_factor_bps, "max_debt_to_cover_raw": str(c.max_debt_to_cover_raw),
                "collateral_seized_raw": str(c.collateral_seized_raw), "protocol_fee_raw": str(c.protocol_fee_raw),
                "breakeven_gas_usd": None if c.breakeven_gas_usd is None else str(c.breakeven_gas_usd),
                "breakeven_slippage_bps": c.breakeven_slippage_bps,
                "max_rational_bid_usd": None if c.max_rational_bid_usd is None else str(c.max_rational_bid_usd),
                "return_on_notional": None if c.return_on_notional is None else str(c.return_on_notional)})
    return out


def _opportunity_out(request: Request, db: Session, c: m.LiquidationCandidate) -> OpportunityOut:
    b = db.get(m.Borrower, c.borrower_id)
    da, ca = db.get(m.Asset, c.debt_asset_id), db.get(m.Asset, c.collateral_asset_id)
    return OpportunityOut(
        id=c.id, network=_slug(request, c.chain_id), borrower=b.address if b else "?", health_factor=hf_str(c.health_factor_wad) or "?",
        debt_asset=da.symbol if da else "?", collateral_asset=ca.symbol if ca else "?",
        repayment=str(to_units(int(c.debt_to_cover_raw), da.decimals)) if da else str(c.debt_to_cover_raw),
        repayment_usd=None if not c.breakdown else c.breakdown.get("debt_to_cover_usd"),
        bonus_bps=c.liquidation_bonus_bps, flash_loan_fee=str(to_units(int(c.flash_premium_raw), da.decimals)) if da else str(c.flash_premium_raw),
        swap_impact_bps=None if not c.breakdown else c.breakdown.get("swap_impact_bps"),
        gas_usd=None if c.gas_cost_usd is None else str(c.gas_cost_usd), net_profit_usd=None if c.net_profit_usd is None else str(c.net_profit_usd),
        status=c.status.value, outcome_class=c.outcome_class.value, block_number=c.block_number,
        incomplete_reasons=c.incomplete_reasons, created_at=c.created_at)
