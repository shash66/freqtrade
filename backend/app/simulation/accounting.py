"""Profit reconciliation for fork executions (Phase 3, item 1 and 4).

A replay is *net profitable* only when every material cost is known. The receiver's SimulatedLiquidation event
gives the on-chain token surplus (swapOut − repaid − premium); execution costs are the measured L2 gas times the
gas price plus the L1 data fee. If the L1 fee is unknown on an L2, the result is `incomplete`, never profitable."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.money import quantize_usd, raw_to_usd

BASE_UNIT = 10**8


@dataclass
class ProfitReconciliation:
    complete: bool
    surplus_raw: int | None            # debt-token surplus measured on the fork (event) or derived
    surplus_matches_event: bool | None  # swapOut − repaid − premium == event surplus
    l2_execution_wei: int | None
    l1_data_wei: int | None
    l1_source: str
    total_fee_wei: int | None
    fee_usd: Decimal | None
    fee_in_debt_raw: int | None
    net_profit_debt_raw: int | None
    net_profit_usd: Decimal | None
    gross_surplus_usd: Decimal | None
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, (int, Decimal)) and v is not None else v) for k, v in self.__dict__.items()}


def reconcile_profit(*, swap_out_raw: int | None, repaid_raw: int | None, premium_raw: int | None, event_surplus_raw: int | None,
                     gas_used: int | None, gas_price_wei: int | None, l1_data_wei: int | None, l1_source: str,
                     native_price_raw: int | None, debt_price_raw: int | None, debt_decimals: int,
                     l1_required: bool = True) -> ProfitReconciliation:
    missing: list[str] = []
    surplus = None
    matches = None
    if swap_out_raw is not None and repaid_raw is not None and premium_raw is not None:
        surplus = swap_out_raw - repaid_raw - premium_raw
        if event_surplus_raw is not None:
            matches = surplus == event_surplus_raw
            surplus = event_surplus_raw  # the event is the measured truth; the arithmetic is the check
    elif event_surplus_raw is not None:
        surplus = event_surplus_raw
    else:
        missing.append("token surplus (swap proceeds, repaid amount or premium unknown)")
    l2 = gas_used * gas_price_wei if gas_used is not None and gas_price_wei is not None else None
    if l2 is None:
        missing.append("L2 execution fee (gas used or gas price unknown)")
    if l1_required and l1_data_wei is None:
        missing.append(f"L1 data fee unknown ({l1_source})")
    total = None if l2 is None or (l1_required and l1_data_wei is None) else l2 + (l1_data_wei or 0)
    if native_price_raw is None:
        missing.append("native token price unknown")
    if debt_price_raw is None:
        missing.append("debt token price unknown")
    fee_usd = fee_debt = net_raw = net_usd = gross_usd = None
    if total is not None and native_price_raw is not None:
        fee_usd = quantize_usd(raw_to_usd(total, 18, native_price_raw))
    if surplus is not None and debt_price_raw is not None:
        gross_usd = quantize_usd(raw_to_usd(abs(surplus), debt_decimals, debt_price_raw)) * (1 if surplus >= 0 else -1)
    if fee_usd is not None and surplus is not None and debt_price_raw is not None:
        fee_debt = int(fee_usd * Decimal(10**debt_decimals) * Decimal(BASE_UNIT) / Decimal(debt_price_raw))
        net_raw = surplus - fee_debt
        net_usd = quantize_usd(raw_to_usd(abs(net_raw), debt_decimals, debt_price_raw)) * (1 if net_raw >= 0 else -1)
    return ProfitReconciliation(complete=not missing, surplus_raw=surplus, surplus_matches_event=matches, l2_execution_wei=l2,
                                l1_data_wei=l1_data_wei, l1_source=l1_source, total_fee_wei=total, fee_usd=fee_usd,
                                fee_in_debt_raw=fee_debt, net_profit_debt_raw=net_raw, net_profit_usd=net_usd,
                                gross_surplus_usd=gross_usd, missing=missing)
