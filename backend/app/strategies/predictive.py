"""Predictive detection (spec §5.4): trigger prices and scenario sensitivities computed from a borrower's
reserve states and reserve parameters. These are predictions from indexed state; eligibility is always
re-checked against the live Pool before anything is simulated."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.money import BPS

BASE_UNIT = 10**8


@dataclass(frozen=True)
class ReserveLeg:
    symbol: str
    address: str
    decimals: int
    price_raw: int
    amount_raw: int                   # aToken balance (collateral) or total debt (debt leg)
    liquidation_threshold_bps: int    # 0 for debt legs
    is_collateral: bool


def _usd(leg: ReserveLeg, price_raw: int | None = None) -> Decimal:
    p = Decimal(price_raw if price_raw is not None else leg.price_raw) / Decimal(BASE_UNIT)
    return Decimal(leg.amount_raw) / Decimal(10**leg.decimals) * p


def health_factor(colls: list[ReserveLeg], debts: list[ReserveLeg], price_overrides: dict[str, int] | None = None,
                  debt_multiplier: Decimal = Decimal(1)) -> Decimal | None:
    po = price_overrides or {}
    weighted = sum((_usd(c, po.get(c.address.lower())) * c.liquidation_threshold_bps / BPS for c in colls), Decimal(0))
    debt = sum((_usd(d, po.get(d.address.lower())) for d in debts), Decimal(0)) * debt_multiplier
    if debt <= 0:
        return None
    return (weighted / debt).quantize(Decimal("0.000001"))


def trigger_prices(colls: list[ReserveLeg], debts: list[ReserveLeg]) -> list[dict]:
    """For each collateral: the price at which HF reaches 1 holding every other price fixed.
    HF = (other_weighted + units_i * p_i * LT_i) / debt = 1  ->  p_i* = (debt - other_weighted) / (units_i * LT_i)."""
    debt = sum((_usd(d) for d in debts), Decimal(0))
    out = []
    for c in colls:
        others = sum((_usd(o) * o.liquidation_threshold_bps / BPS for o in colls if o is not c), Decimal(0))
        units = Decimal(c.amount_raw) / Decimal(10**c.decimals)
        lt = Decimal(c.liquidation_threshold_bps) / BPS
        if units <= 0 or lt <= 0 or debt <= 0:
            out.append({"collateral": c.symbol, "trigger_price_usd": None, "note": "not computable"})
            continue
        p_star = (debt - others) / (units * lt)
        cur = Decimal(c.price_raw) / Decimal(BASE_UNIT)
        out.append({"collateral": c.symbol, "address": c.address, "current_price_usd": str(cur.quantize(Decimal("0.00000001"))),
                    "trigger_price_usd": str(max(p_star, Decimal(0)).quantize(Decimal("0.00000001"))),
                    "drop_to_trigger_pct": None if cur <= 0 else str(((cur - p_star) / cur * 100).quantize(Decimal("0.01"))),
                    "note": "other collateral prices and debt held constant; prediction only"})
    return out


def scenarios(colls: list[ReserveLeg], debts: list[ReserveLeg], shocks_pct=(-1, -5, -10, -20),
              annual_borrow_rate: Decimal | None = None, horizon_days: int = 7) -> dict:
    base_hf = health_factor(colls, debts)
    per_collateral = []
    for c in colls:
        rows = []
        for s in shocks_pct:
            p = int(c.price_raw * (100 + s) // 100)
            rows.append({"shock_pct": s, "health_factor": str(health_factor(colls, debts, {c.address.lower(): p}))})
        per_collateral.append({"collateral": c.symbol, "rows": rows})
    correlated = []
    for s in shocks_pct:
        po = {c.address.lower(): int(c.price_raw * (100 + s) // 100) for c in colls}
        correlated.append({"shock_pct": s, "health_factor": str(health_factor(colls, debts, po))})
    accrual = None
    if annual_borrow_rate is not None:
        mult = Decimal(1) + annual_borrow_rate * Decimal(horizon_days) / Decimal(365)
        accrual = {"horizon_days": horizon_days, "annual_borrow_rate": str(annual_borrow_rate), "debt_multiplier": str(mult.quantize(Decimal("0.000001"))),
                   "health_factor": str(health_factor(colls, debts, None, mult))}
    return {"health_factor_now": None if base_hf is None else str(base_hf), "trigger_prices": trigger_prices(colls, debts),
            "single_collateral_shocks": per_collateral, "correlated_shocks": correlated, "interest_accrual": accrual,
            "note": "predictions from indexed state and current oracle prices; not a liquidatability confirmation"}
