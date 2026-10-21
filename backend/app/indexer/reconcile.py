"""Reconcile indexed borrowers against authoritative on-chain reads (spec §5.3), adaptively:
every run refreshes account data for a bounded batch ordered by priority, and reads per-reserve detail
only for borrowers near liquidation. Oracle prices and reserve configuration are refreshed each run."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.money import WAD, health_factor
from app.database.base import get_sessionmaker
from app.database import models as m

log = get_logger(__name__)

BATCH = 200
DETAIL_BATCH = 40


def priority_score(hf_wad: int, total_debt_base: int, base_unit: int) -> Decimal:
    """Higher = more urgent. HF proximity to 1 dominates; debt size (in base currency) is a tiebreaker.
    Positions with no debt get 0."""
    if total_debt_base <= 0 or hf_wad >= 2**256 - 1:
        return Decimal(0)
    hf = Decimal(hf_wad) / Decimal(WAD)
    debt_usd = Decimal(total_debt_base) / Decimal(base_unit)
    distance = max(hf - Decimal(1), Decimal("0.0001"))  # HF < 1 -> eligible now -> max urgency
    if hf < 1:
        distance = Decimal("0.0001")
    return (Decimal(1) / distance) * (Decimal(1) + debt_usd.ln() if debt_usd > 1 else Decimal(1))


def refresh_reserves(db: Session, net: NetworkConfig, reader: AaveReader, block: int) -> dict[str, m.Asset]:
    assets: dict[str, m.Asset] = {}
    prices = {}
    reserves = reader.reserves_list(block)
    try:
        prices = reader.asset_prices(reserves, block)
    except Exception as e:
        log.warning("oracle_prices_unavailable", network=net.slug, error=str(e)[:120])
    known = {r.address.lower(): r for r in net.market.reserves}
    for addr in reserves:
        cfg = reader.reserve_config(addr, block)
        a_tok, _s, v_tok = reader.reserve_tokens(addr, block)
        sym = known[addr.lower()].symbol if addr.lower() in known else addr[:10]
        stmt = insert(m.Asset).values(
            chain_id=net.chain_id, address=addr, symbol=sym, decimals=cfg.decimals, a_token=a_tok, variable_debt_token=v_tok,
            ltv_bps=cfg.ltv_bps, liquidation_threshold_bps=cfg.liquidation_threshold_bps,
            liquidation_bonus_bps=cfg.liquidation_bonus_bps, liquidation_protocol_fee_bps=cfg.liquidation_protocol_fee_bps,
            reserve_factor_bps=cfg.reserve_factor_bps, emode_category=cfg.emode_category_legacy or None,
            usage_as_collateral_enabled=cfg.liquidation_threshold_bps > 0, borrowing_enabled=cfg.borrowing_enabled,
            is_active=cfg.active, is_frozen=cfg.frozen, is_paused=cfg.paused, config_observed_block=block)
        upd = {k: getattr(stmt.excluded, k) for k in ("decimals", "a_token", "variable_debt_token", "ltv_bps",
               "liquidation_threshold_bps", "liquidation_bonus_bps", "liquidation_protocol_fee_bps", "reserve_factor_bps",
               "emode_category", "usage_as_collateral_enabled", "borrowing_enabled", "is_active", "is_frozen", "is_paused",
               "config_observed_block")}
        db.execute(stmt.on_conflict_do_update(constraint="uq_asset_chain_address", set_=upd))
        if addr in prices:
            db.add(m.OraclePriceObservation(chain_id=net.chain_id, asset_address=addr, price_raw=prices[addr],
                                            price_decimals=8, block_number=block))
    db.commit()
    for a in db.scalars(select(m.Asset).where(m.Asset.chain_id == net.chain_id)):
        assets[a.address.lower()] = a
    return assets


def reconcile_network(net: NetworkConfig, batch: int = BATCH) -> dict:
    s = get_settings()
    client = RpcClient(net.slug, net.rpc_http, s.rpc_timeout_seconds, s.rpc_max_retries)
    if not client.configured:
        return {"skipped": True, "reason": "no RPC configured"}
    reader = AaveReader(net, client)
    block = int(client.call(lambda: reader.w3.eth.block_number))
    base_unit = reader.base_currency_unit(block)
    alert_wad = int(s.position_health_factor_alert * WAD)
    out = {"block": block, "reconciled": 0, "detail_reads": 0, "eligible": 0, "near": 0, "errors": 0}
    with get_sessionmaker()() as db:
        assets = refresh_reserves(db, net, reader, block)
        # Oldest-reconciled first, but always include the current most urgent ones.
        urgent = db.scalars(select(m.Borrower).where(m.Borrower.chain_id == net.chain_id, m.Borrower.is_active.is_(True))
                            .order_by(m.Borrower.priority_score.desc().nulls_last()).limit(batch // 2)).all()
        stale = db.scalars(select(m.Borrower).where(m.Borrower.chain_id == net.chain_id, m.Borrower.is_active.is_(True))
                           .order_by(m.Borrower.last_reconciled_at.asc().nulls_first()).limit(batch)).all()
        seen, todo = set(), []
        for b in urgent + stale:
            if b.id not in seen:
                seen.add(b.id)
                todo.append(b)
        todo = todo[:batch]
        now = datetime.now(timezone.utc)
        detail_done = 0
        for b in todo:
            try:
                acct = reader.user_account_data(b.address, block)
            except Exception as e:
                out["errors"] += 1
                log.warning("account_read_failed", network=net.slug, user=b.address, error=str(e)[:120])
                continue
            changed = (b.health_factor_wad != acct.health_factor_wad or b.total_debt_base != acct.total_debt_base)
            b.health_factor_wad = acct.health_factor_wad
            b.total_collateral_base = acct.total_collateral_base
            b.total_debt_base = acct.total_debt_base
            b.available_borrows_base = acct.available_borrows_base
            b.current_liquidation_threshold_bps = acct.current_liquidation_threshold_bps
            b.ltv_bps = acct.ltv_bps
            b.last_reconciled_block = block
            b.last_reconciled_at = now
            b.priority_score = priority_score(acct.health_factor_wad, acct.total_debt_base, base_unit)
            b.is_active = acct.total_debt_base > 0
            out["reconciled"] += 1
            if acct.total_debt_base > 0 and acct.health_factor_wad < WAD:
                out["eligible"] += 1
            elif acct.total_debt_base > 0 and acct.health_factor_wad < alert_wad:
                out["near"] += 1
            near = acct.total_debt_base > 0 and acct.health_factor_wad < alert_wad
            if near and detail_done < DETAIL_BATCH:
                detail_done += 1
                reserves_state = []
                try:
                    b.emode_category = reader.user_emode(b.address, block)
                    for addr, asset in assets.items():
                        urd = reader.user_reserve_data(asset.address, b.address, block)
                        if urd.a_token_balance == 0 and urd.variable_debt == 0 and urd.stable_debt == 0:
                            continue
                        stmt = insert(m.BorrowerReserveState).values(
                            borrower_id=b.id, asset_id=asset.id, a_token_balance=urd.a_token_balance,
                            variable_debt=urd.variable_debt, stable_debt=urd.stable_debt,
                            usage_as_collateral=urd.usage_as_collateral, observed_block=block)
                        db.execute(stmt.on_conflict_do_update(constraint="uq_borrower_reserve", set_={
                            "a_token_balance": stmt.excluded.a_token_balance, "variable_debt": stmt.excluded.variable_debt,
                            "stable_debt": stmt.excluded.stable_debt, "usage_as_collateral": stmt.excluded.usage_as_collateral,
                            "observed_block": block}))
                        reserves_state.append({"asset": asset.address, "symbol": asset.symbol,
                                               "a_token_balance": str(urd.a_token_balance), "variable_debt": str(urd.variable_debt),
                                               "stable_debt": str(urd.stable_debt), "collateral": urd.usage_as_collateral})
                    out["detail_reads"] += 1
                except Exception as e:
                    out["errors"] += 1
                    log.warning("reserve_detail_failed", network=net.slug, user=b.address, error=str(e)[:120])
                if changed or near:
                    db.add(m.PositionSnapshot(borrower_id=b.id, chain_id=net.chain_id, block_number=block,
                                              health_factor_wad=acct.health_factor_wad,
                                              total_collateral_base=acct.total_collateral_base,
                                              total_debt_base=acct.total_debt_base, reserves=reserves_state))
        db.commit()
    log.info("reconcile_done", network=net.slug, **{k: v for k, v in out.items()})
    return out


def hf_str(hf_wad: int | None) -> str | None:
    return None if hf_wad is None else str(health_factor(int(hf_wad)))
