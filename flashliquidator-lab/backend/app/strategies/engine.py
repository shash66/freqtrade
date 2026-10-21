"""Candidate builder (spec §6, steps 1-11): turns reconciled borrower state into LiquidationCandidate rows
with a full, reproducible breakdown and explicit incomplete states."""
from __future__ import annotations

import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig
from app.chains.l1fees import estimate_l1_fee
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.money import WAD, usd_to_raw
from app.database import models as m
from app.database.base import get_sessionmaker
from app.strategies import profitability as pf
from app.strategies.quotes import UniswapV3Quoter, price_impact_bps


class EModeCache:
    """Per-run cache of e-mode category collateral parameters. Returns None when unreadable (caller marks incomplete)."""

    def __init__(self, reader: AaveReader, block: int):
        self.reader, self.block, self._cache = reader, block, {}

    def bonus_for(self, category: int, collateral: str) -> int | None:
        key = (category, collateral.lower())
        if key not in self._cache:
            try:
                self._cache[key] = self.reader.emode_collateral_bonus(category, collateral, self.block)
            except Exception as e:  # unreadable -> None, never a guess
                log.warning("emode_read_failed", category=category, error=str(e)[:120])
                self._cache[key] = None
        return self._cache[key]

log = get_logger(__name__)
NATIVE_PROXY = {8453: "0x4200000000000000000000000000000000000006", 42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}  # WETH


def _reserve_params(a: m.Asset, price: int) -> pf.ReserveParams:
    return pf.ReserveParams(address=a.address, symbol=a.symbol, decimals=a.decimals, price_raw=price,
                            liquidation_bonus_bps=a.liquidation_bonus_bps or 0, liquidation_protocol_fee_bps=a.liquidation_protocol_fee_bps or 0,
                            liquidation_threshold_bps=a.liquidation_threshold_bps or 0, active=bool(a.is_active), paused=bool(a.is_paused))


def build_candidates(net: NetworkConfig, limit: int = 50) -> dict:
    s = get_settings()
    client = RpcClient(net.slug, net.rpc_http, s.rpc_timeout_seconds, s.rpc_max_retries)
    if not client.configured:
        return {"skipped": True, "reason": "no RPC configured"}
    reader = AaveReader(net, client)
    t_observe = time.monotonic()
    block = int(client.call(lambda: reader.w3.eth.block_number))
    blk = client.call(reader.w3.eth.get_block, block)
    block_hash = blk["hash"].hex() if hasattr(blk["hash"], "hex") else str(blk["hash"])
    block_hash = block_hash if block_hash.startswith("0x") else "0x" + block_hash
    gas_price = int(client.call(lambda: reader.w3.eth.gas_price))
    premium_total, _ = reader.flashloan_premium_bps(block)
    quoter = UniswapV3Quoter(net.chain_id, client)
    emode = EModeCache(reader, block)
    rules = pf.LiquidationRules()
    out = {"block": block, "candidates": 0, "incomplete": 0, "invalid": 0}
    with get_sessionmaker()() as db:
        assets = {a.address.lower(): a for a in db.scalars(select(m.Asset).where(m.Asset.chain_id == net.chain_id))}
        if not assets:
            return {"skipped": True, "reason": "no reserves indexed yet"}
        prices = reader.asset_prices([a.address for a in assets.values()], block)
        prices = {k.lower(): v for k, v in prices.items()}
        native_price = prices.get(NATIVE_PROXY.get(net.chain_id, "").lower())
        eligible = db.scalars(select(m.Borrower).where(m.Borrower.chain_id == net.chain_id, m.Borrower.is_active.is_(True),
                                                       m.Borrower.health_factor_wad < WAD, m.Borrower.total_debt_base > 0)
                              .order_by(m.Borrower.total_debt_base.desc()).limit(limit)).all()
        for b in eligible:
            states = db.execute(select(m.BorrowerReserveState, m.Asset).join(m.Asset, m.Asset.id == m.BorrowerReserveState.asset_id)
                                .where(m.BorrowerReserveState.borrower_id == b.id)).all()
            debts = [(r, a) for r, a in states if (r.variable_debt + r.stable_debt) > 0]
            colls = [(r, a) for r, a in states if r.a_token_balance > 0 and r.usage_as_collateral]
            if not debts or not colls:
                _store(db, net, b, block, block_hash, None, None, None, rules, status=m.CandidateStatus.incomplete,
                       reasons=["per-reserve state not yet read for this borrower"], debt_asset=None, coll_asset=None)
                out["incomplete"] += 1
                continue
            # Prefer USDC debt / WETH collateral (spec §2.1), else the largest by USD value.
            def usd(r, a, amt):
                return Decimal(amt) * Decimal(prices.get(a.address.lower(), 0)) / Decimal(10**a.decimals)
            debts.sort(key=lambda t: (t[1].symbol != "USDC", -usd(t[0], t[1], t[0].variable_debt + t[0].stable_debt)))
            colls.sort(key=lambda t: (t[1].symbol != "WETH", -usd(t[0], t[1], t[0].a_token_balance)))
            (dr, da), (cr, ca) = debts[0], colls[0]
            dp, cp = prices.get(da.address.lower()), prices.get(ca.address.lower())
            reasons = []
            if not dp or not cp:
                # No math on placeholder prices: record the gap and move on (project rule: never invent values).
                _store(db, net, b, block, block_hash, None, None, None, rules, status=m.CandidateStatus.incomplete,
                       reasons=["oracle price unavailable for debt or collateral asset"], debt_asset=da, coll_asset=ca)
                out["incomplete"] += 1
                continue
            emode_bonus = None
            if b.emode_category:
                emode_bonus = emode.bonus_for(int(b.emode_category), ca.address)
                if emode_bonus is None:
                    reasons.append(f"user is in e-mode category {b.emode_category}; category bonus for {ca.symbol} could not be read")
            pos = pf.PositionInputs(health_factor_wad=int(b.health_factor_wad), total_collateral_base=int(b.total_collateral_base or 0),
                                    total_debt_base=int(b.total_debt_base), debt=_reserve_params(da, dp), collateral=_reserve_params(ca, cp),
                                    user_debt_raw=int(dr.variable_debt + dr.stable_debt), user_collateral_raw=int(cr.a_token_balance),
                                    collateral_enabled=cr.usage_as_collateral, emode_bonus_bps=emode_bonus)
            elig = pf.check_eligibility(pos, rules)
            if not elig.eligible:
                _store(db, net, b, block, block_hash, pos, None, None, rules, status=m.CandidateStatus.invalid, reasons=elig.reasons,
                       debt_asset=da, coll_asset=ca)
                out["invalid"] += 1
                continue
            max_flash_raw = usd_to_raw(s.max_flash_loan_usd, da.decimals, dp) if dp else None
            outcome, problems = pf.choose_debt_to_cover(pos, rules, elig, max_flash_raw)
            if problems:
                reasons += problems
            q = quoter.quote_exact_in(ca.address, da.address, outcome.collateral_to_liquidator_raw, block)
            impact = None
            if q.status == "ok" and dp and cp:
                impact = price_impact_bps(outcome.collateral_to_liquidator_raw, ca.decimals, cp, q.amount_out_raw, da.decimals, dp)
            sq = m.SwapQuote(chain_id=net.chain_id, token_in=ca.address, token_out=da.address, amount_in_raw=outcome.collateral_to_liquidator_raw,
                             amount_out_raw=q.amount_out_raw, source=q.source, route={"fee_tier": q.fee_tier}, price_impact_bps=impact,
                             block_number=block, status=q.status, error=q.error)
            db.add(sq)
            db.flush()
            gas_units = net.gas.base_gas_units_liquidation + (q.gas_estimate or 0)
            # L1 data fee: priced on the receiver's liquidate(params) calldata (10 words + selector)
            calldata = reader.pool.functions.liquidationCall(reader.w3.to_checksum_address(ca.address), reader.w3.to_checksum_address(da.address),
                                                             reader.w3.to_checksum_address(b.address), outcome.debt_to_cover_raw, False)._encode_transaction_data()
            l1 = estimate_l1_fee(net.chain_id, client, net.market.pool, bytes.fromhex(calldata[2:]) + b"\x00" * 160, gas_units, gas_price, block)
            costs = pf.CostInputs(flash_premium_total_bps=premium_total, gas_units=gas_units,
                                  gas_price_wei=gas_price, native_price_raw=native_price, l1_data_fee_wei=l1.l1_data_wei,
                                  l1_fee_required=l1.source != "not_applicable",
                                  swap_out_raw=q.amount_out_raw, swap_source=q.source, swap_impact_bps=impact)
            if l1.source == "unknown":
                reasons.append("L1 data fee could not be estimated: " + "; ".join(l1.notes))
            report = pf.profit_report(pos, rules, outcome, costs, prior_incomplete=reasons)
            report.breakdown.update(l1_data_fee_wei="" if l1.l1_data_wei is None else str(l1.l1_data_wei), fee_source=l1.source,
                                    fee_method=l1.method, gas_price_wei=str(gas_price), gas_units=str(gas_units))
            status = m.CandidateStatus.incomplete if not report.complete else m.CandidateStatus.candidate
            cand = _store(db, net, b, block, block_hash, pos, outcome, report, rules, status=status, reasons=report.incomplete_reasons,
                          debt_asset=da, coll_asset=ca, elig=elig, swap_quote_id=sq.id)
            if cand is not None:
                lat = time.monotonic() - t_observe
                cand.data_sources = dict(cand.data_sources or {}, detection_to_candidate_seconds=round(lat, 3))
                from app.strategies.shadow import LATENCY, OPPORTUNITIES
                LATENCY.labels(net.slug).observe(lat)
                OPPORTUNITIES.labels(net.slug).inc()
            out["candidates" if status == m.CandidateStatus.candidate else "incomplete"] += 1
        db.commit()
    return out


def _store(db: Session, net, b, block, block_hash, pos, outcome, report, rules, *, status, reasons, debt_asset, coll_asset,
           elig=None, swap_quote_id=None) -> m.LiquidationCandidate | None:
    if debt_asset is None or coll_asset is None:
        return None
    c = m.LiquidationCandidate(
        chain_id=net.chain_id, borrower_id=b.id, block_number=block, block_hash=block_hash, status=status,
        outcome_class=m.OutcomeClass.theoretical, health_factor_wad=int(b.health_factor_wad), debt_asset_id=debt_asset.id,
        collateral_asset_id=coll_asset.id, debt_to_cover_raw=outcome.debt_to_cover_raw if outcome else 0,
        max_debt_to_cover_raw=elig.max_debt_to_cover_raw if elig else 0, close_factor_bps=elig.close_factor_bps if elig else 0,
        collateral_seized_raw=outcome.collateral_seized_raw if outcome else 0, protocol_fee_raw=outcome.protocol_fee_raw if outcome else 0,
        liquidation_bonus_bps=outcome.effective_bonus_bps if outcome else (coll_asset.liquidation_bonus_bps or 0),
        flash_premium_raw=(report.flash_premium_raw if report and report.flash_premium_raw is not None else 0), swap_quote_id=swap_quote_id,  # 0 only with an incomplete_reasons entry
        swap_out_raw=None if not report or report.outcome is None else (int(report.breakdown["swap_out"]) if report.breakdown["swap_out"] else None),
        gas_cost_usd=report.gas_cost_usd if report else None, gross_bonus_usd=report.gross_bonus_usd if report else None,
        gas_units=int(report.breakdown["gas_units"]) if report and report.breakdown.get("gas_units") else None,
        gas_price_wei=int(report.breakdown["gas_price_wei"]) if report and report.breakdown.get("gas_price_wei") else None,
        l1_data_fee_wei=int(report.breakdown["l1_data_fee_wei"]) if report and report.breakdown.get("l1_data_fee_wei") else None,
        fee_source=report.breakdown.get("fee_source") if report else None,
        net_profit_debt_raw=report.net_profit_debt_raw if report else None, net_profit_usd=report.net_profit_usd if report else None,
        return_on_notional=report.return_on_notional if report else None, breakeven_gas_usd=report.breakeven_gas_usd if report else None,
        breakeven_slippage_bps=report.breakeven_slippage_bps if report else None, max_rational_bid_usd=report.max_rational_bid_usd if report else None,
        incomplete_reasons=list(reasons) or None,
        breakdown=(dict(report.breakdown, sensitivity=report.sensitivity, failed_attempt_cost_usd=str(report.failed_attempt_cost_usd)) if report else None),
        data_sources={"oracle": net.market.oracle, "pool": net.market.pool, "block": block, "block_hash": block_hash,
                      "rules_version": rules.version, "address_book": "bgd-labs/aave-address-book"})
    db.add(c)
    return c
