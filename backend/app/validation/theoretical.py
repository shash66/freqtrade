"""Verified theoretical profit of recorded liquidations (outcome class: theoretical).

For every LiquidationCall event recorded under the verified Pool, value the amounts the winner actually
repaid and seized at the Aave oracle prices *of the winning block* (archive read), and subtract the winner's
receipt-verified transaction fee (L2 execution + L1 data). This is what the protocol handed over, before any
swap: it is the upper bound a liquidator could have realised with a perfect, free exit of the collateral.

    theoretical_gross_usd = seized_raw * price_collateral - repaid_raw * price_debt      (oracle base = USD 1e8)
    theoretical_net_usd   = theoretical_gross_usd - winner_fee_usd (verified)             (null when the fee is unknown)

It is reported next to, and never mixed with, the simulated profit (our receiver on a fork) and the modeled
competition-adjusted profit.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal

from sqlalchemy import select
from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.money import quantize_usd
from app.database import models as m
from app.database.base import get_sessionmaker

NATIVE_PROXY = {8453: "0x4200000000000000000000000000000000000006", 42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}


def theoretical_for_network(slug: str, limit: int = 100) -> dict:
    s = get_settings()
    net = build_networks(s)[slug]
    if not net.archive_rpc:
        return {"network": slug, "status": "BLOCKED", "reason": "archive RPC required for prices at the winning block", "events": []}
    client = RpcClient(f"{slug}-archive", net.archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries)
    reader = AaveReader(net, client)
    with get_sessionmaker()() as db:
        liqs = db.scalars(select(m.HistoricalLiquidation).join(m.ProtocolDeployment, m.ProtocolDeployment.id == m.HistoricalLiquidation.deployment_id)
                          .where(m.HistoricalLiquidation.chain_id == net.chain_id, m.ProtocolDeployment.pool_address.ilike(net.market.pool),
                                 (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0))
                          .order_by(m.HistoricalLiquidation.block_number.desc()).limit(limit)).all()
        assets = {a.address.lower(): a for a in db.scalars(select(m.Asset).where(m.Asset.chain_id == net.chain_id))}
    base_unit = Decimal(reader.base_currency_unit())
    out = {"network": slug, "status": "EXECUTED", "outcome_class": "theoretical", "events": [], "totals": {}}
    gross_total = net_total = Decimal(0)
    n_net = 0
    price_cache: dict[tuple[int, str], int] = {}
    for h in liqs:
        blk = h.block_number
        ca, da, na = Web3.to_checksum_address(h.collateral_asset), Web3.to_checksum_address(h.debt_asset), Web3.to_checksum_address(NATIVE_PROXY[net.chain_id])
        need = [a for a in (ca, da, na) if (blk, a) not in price_cache]
        if need:
            prices = reader.asset_prices(need, block=blk)
            for a, p in prices.items():
                price_cache[(blk, a)] = p
        pc, pd, pn = price_cache[(blk, ca)], price_cache[(blk, da)], price_cache[(blk, na)]
        c_dec = assets[h.collateral_asset.lower()].decimals if h.collateral_asset.lower() in assets else None
        d_dec = assets[h.debt_asset.lower()].decimals if h.debt_asset.lower() in assets else None
        row = {"tx": h.tx_hash, "log_index": h.log_index, "block": blk, "liquidator": h.liquidator, "borrower": h.borrower,
               "collateral_asset": h.collateral_asset, "debt_asset": h.debt_asset, "collateral_seized_raw": str(h.collateral_seized_raw),
               "debt_repaid_raw": str(h.debt_repaid_raw), "price_collateral_raw": pc, "price_debt_raw": pd, "price_native_raw": pn,
               "winner_fee_wei": str(h.total_fee_wei) if h.total_fee_wei is not None else None, "winner_fee_source": h.fee_source}
        if c_dec is None or d_dec is None:
            row["status"] = "INCOMPLETE: asset decimals unknown"
            out["events"].append(row)
            continue
        seized_usd = Decimal(int(h.collateral_seized_raw)) * Decimal(pc) / (Decimal(10**c_dec) * base_unit)
        repaid_usd = Decimal(int(h.debt_repaid_raw)) * Decimal(pd) / (Decimal(10**d_dec) * base_unit)
        gross = quantize_usd(seized_usd - repaid_usd)
        row.update({"collateral_seized_usd": str(quantize_usd(seized_usd)), "debt_repaid_usd": str(quantize_usd(repaid_usd)),
                    "theoretical_gross_usd": str(gross), "bonus_pct_of_repaid": str(quantize_usd(gross / repaid_usd * 100)) if repaid_usd else None})
        gross_total += gross
        if h.total_fee_wei is not None and h.fee_source == "verified":
            # the fee is paid once per transaction; attribute it to the first event of the tx only
            first = min(x.log_index for x in liqs if x.tx_hash == h.tx_hash)
            fee_usd = quantize_usd(Decimal(int(h.total_fee_wei)) * Decimal(pn) / (Decimal(10**18) * base_unit)) if h.log_index == first else Decimal(0)
            row["winner_fee_usd"] = str(fee_usd)
            row["theoretical_net_usd"] = str(quantize_usd(gross - fee_usd))
            net_total += gross - fee_usd
            n_net += 1
        else:
            row["theoretical_net_usd"] = None
            row["status"] = "net unknown: winner fee not verified"
        out["events"].append(row)
    out["totals"] = {"events": len(liqs), "unique_txs": len({h.tx_hash for h in liqs}), "theoretical_gross_usd": str(quantize_usd(gross_total)),
                     "theoretical_net_usd_verified_fees": str(quantize_usd(net_total)), "events_with_verified_fee": n_net,
                     "note": "gross = oracle value of seized collateral minus repaid debt at the winning block; net subtracts the winner's receipt-verified fee once per transaction"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="base")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    out = theoretical_for_network(a.network, a.limit)
    txt = json.dumps(out, indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(txt)
    print(json.dumps(out["totals"] if out.get("totals") else out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
