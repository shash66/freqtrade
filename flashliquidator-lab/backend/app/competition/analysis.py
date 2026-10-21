"""Competitor statistics from recorded on-chain liquidations (outcome class: onchain_actual)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models as m


def _pct(xs: list[int], q: float) -> int | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def competitor_summary(db: Session, chain_ids: dict[str, int], days: int = 30) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = {"outcome_class": "onchain_actual", "window_days": days, "networks": {}, "notes": [
        "timestamps are block timestamps; they are not execution latency",
        "eligibility-to-liquidation blocks are only reported where this system observed eligibility first (shadow mode)",
    ]}
    for slug, cid in chain_ids.items():
        rows = db.scalars(select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id == cid,
                                                                (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0),
                                                                (m.HistoricalLiquidation.block_timestamp >= since) | (m.HistoricalLiquidation.block_timestamp.is_(None)))).all()
        assets = {a.address.lower(): a for a in db.scalars(select(m.Asset).where(m.Asset.chain_id == cid))}
        liq = Counter(r.liquidator for r in rows)
        total = len(rows)
        top = liq.most_common(5)
        gas = [r.gas_used for r in rows if r.gas_used]
        fees = [r.effective_gas_price_wei for r in rows if r.effective_gas_price_wei]
        pairs = Counter((assets.get(r.debt_asset.lower()).symbol if r.debt_asset.lower() in assets else r.debt_asset[:8],
                         assets.get(r.collateral_asset.lower()).symbol if r.collateral_asset.lower() in assets else r.collateral_asset[:8]) for r in rows)
        by_day: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.block_timestamp:
                by_day[r.block_timestamp.date().isoformat()] += 1
        obs = db.scalars(select(m.CompetitorObservation).where(m.CompetitorObservation.chain_id == cid,
                                                               m.CompetitorObservation.blocks_to_liquidation.is_not(None))).all()
        out["networks"][slug] = {
            "liquidations": total, "unique_liquidators": len(liq),
            "top_liquidators": [{"address": a, "count": c, "share": round(c / total, 4) if total else None} for a, c in top],
            "top5_share": round(sum(c for _, c in top) / total, 4) if total else None,
            "gas_used": {"median": int(median(gas)) if gas else None, "p90": _pct(gas, 0.9), "n": len(gas)},
            "effective_gas_price_wei": {"median": int(median(fees)) if fees else None, "p90": _pct(fees, 0.9), "n": len(fees)},
            "asset_pairs": [{"debt": d, "collateral": c, "count": n} for (d, c), n in pairs.most_common(10)],
            "liquidations_by_day": dict(sorted(by_day.items())),
            "blocks_eligibility_to_liquidation": {"n": len(obs), "median": median([o.blocks_to_liquidation for o in obs]) if obs else None,
                                                  "note": "only from shadow-mode observations" if obs else "no shadow observations yet"},
        }
    return out
