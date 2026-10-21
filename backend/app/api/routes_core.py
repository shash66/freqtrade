from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.schemas import Health, NetworkOut, Overview, RpcStatus, StrategyPerf
from app.core.config import Settings, get_settings
from app.core.money import WAD
from app.core.security import rate_limit, require_admin
from app.database.base import get_db
from app.database import models as m

router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])


def _network_out(request: Request, slug: str) -> NetworkOut:
    net = request.app.state.networks[slug]
    clients = request.app.state.rpc_clients[slug]
    rpc = []
    for kind, client in clients.items():
        h = request.app.state.rpc_health.get((slug, kind))
        rpc.append(RpcStatus(kind=kind, configured=client.configured, ok=bool(h and h.ok),
                             latency_ms=h.latency_ms if h else None, block_number=h.block_number if h else None,
                             chain_id=h.chain_id if h else None, error=h.error if h else None))
    ver = request.app.state.deployment_verification.get(slug, {})
    return NetworkOut(slug=slug, chain_id=net.chain_id, name=net.name, native_symbol=net.native_symbol,
                      explorer_url=net.explorer_url, enabled=net.enabled, has_archive_rpc=net.has_archive_rpc,
                      pool_address=net.market.pool, oracle_address=net.market.oracle,
                      address_book_version=ver.get("address_book_version", ""), rpc=rpc,
                      deployment_verified=bool(ver.get("verified")), verification_error=ver.get("error"))


@router.get("/health", response_model=Health)
def health(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    redis_ok = True
    try:
        request.app.state.redis.ping()
    except Exception:
        redis_ok = False
    nets = {slug: bool(request.app.state.rpc_health.get((slug, "http")) and request.app.state.rpc_health[(slug, "http")].ok)
            for slug in request.app.state.networks}
    status = "ok" if db_ok and redis_ok and any(nets.values()) else "degraded"
    return Health(status=status, version=settings.app_version, app_mode=settings.app_mode, database=db_ok,
                  redis=redis_ok, networks=nets, time=datetime.now(timezone.utc))


@router.get("/networks", response_model=list[NetworkOut])
def networks(request: Request):
    return [_network_out(request, slug) for slug in request.app.state.networks]


@router.get("/overview", response_model=Overview)
def overview(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    nets = [_network_out(request, slug) for slug in request.app.state.networks]
    alert_wad = int(settings.position_health_factor_alert * WAD)
    active, near, eligible = {}, {}, {}
    for slug, net in request.app.state.networks.items():
        cid = net.chain_id
        base = select(func.count()).select_from(m.Borrower).where(m.Borrower.chain_id == cid, m.Borrower.is_active.is_(True))
        active[slug] = db.scalar(base) or 0
        near[slug] = db.scalar(base.where(m.Borrower.health_factor_wad < alert_wad, m.Borrower.health_factor_wad >= WAD)) or 0
        eligible[slug] = db.scalar(base.where(m.Borrower.health_factor_wad < WAD, m.Borrower.total_debt_base > 0)) or 0
    prof = db.execute(
        select(func.count(), func.coalesce(func.sum(m.SimulationResult.net_profit_usd), 0))
        .select_from(m.SimulationResult).join(m.SimulationRun, m.SimulationRun.id == m.SimulationResult.run_id)
        .where(m.SimulationResult.success.is_(True), m.SimulationResult.net_profit_usd > 0,
               m.SimulationRun.outcome_class == m.OutcomeClass.simulated)
    ).one()
    strategies = []
    for sd in db.scalars(select(m.StrategyDefinition).order_by(m.StrategyDefinition.key)):
        last = db.scalars(select(m.StrategyRun).where(m.StrategyRun.strategy_id == sd.id)
                          .order_by(m.StrategyRun.created_at.desc()).limit(1)).first()
        n = db.scalar(select(func.count()).select_from(m.StrategyRun).where(m.StrategyRun.strategy_id == sd.id)) or 0
        strategies.append(StrategyPerf(key=sd.key, name=sd.name, runs=n, last_run_at=last.finished_at if last else None,
                                       summary=last.summary if last else None))
    indexer = {}
    for cp in db.scalars(select(m.IndexerCheckpoint)):
        slug = next((s for s, n in request.app.state.networks.items() if n.chain_id == cp.chain_id), str(cp.chain_id))
        indexer[slug] = {"stream": cp.stream, "last_block": cp.last_block, "updated_at": cp.updated_at.isoformat()}
    sim = request.app.state.simulation_health()
    return Overview(networks=nets, active_borrowers=active, near_liquidation_positions=near, eligible_positions=eligible,
                    profitable_simulated_opportunities=int(prof[0]), estimated_total_simulated_profit_usd=prof[1],
                    strategies=strategies, simulation_service=sim, indexer=indexer,
                    generated_at=datetime.now(timezone.utc))


@router.get("/metrics", dependencies=[Depends(require_admin)])
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/settings/public")
def public_settings(settings: Settings = Depends(get_settings)):
    """Non-secret runtime configuration for the Settings page. RPC URLs are never returned."""
    return {
        "app_mode": settings.app_mode, "live_trading_enabled": settings.live_trading_enabled,
        "read_only": True, "min_net_profit_usd": str(settings.min_net_profit_usd),
        "max_flash_loan_usd": str(settings.max_flash_loan_usd), "min_flash_loan_usd": str(settings.min_flash_loan_usd),
        "position_health_factor_alert": str(settings.position_health_factor_alert),
        "monitor_interval_seconds": settings.monitor_interval_seconds,
        "indexer_poll_interval_seconds": settings.indexer_poll_interval_seconds,
        "indexer_max_block_range": settings.indexer_max_block_range, "reorg_safety_blocks": settings.reorg_safety_blocks,
        "simulation_concurrency": settings.simulation_concurrency, "raw_data_retention_days": settings.raw_data_retention_days,
        "rpc_configured": {slug: bool(settings.rpc_for(slug)[0]) for slug in ("base", "arbitrum", "ethereum")},
        "archive_rpc_configured": {slug: bool(settings.rpc_for(slug)[2]) for slug in ("base", "arbitrum", "ethereum")},
    }
