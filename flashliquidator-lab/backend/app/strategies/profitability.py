"""Liquidation opportunity math (spec §6). Pure functions over ints (base units) and Decimals.

Formulas follow aave-v3-origin LiquidationLogic.sol. The close factor and dust rules changed in Aave v3.3,
so they are parameters of `LiquidationRules`, never assumed. Where an input needed for a number is missing,
the result carries an `incomplete_reasons` entry instead of a made-up value."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.money import BPS, WAD, percent_div, percent_mul, quantize_usd, raw_to_usd

BASE_UNIT = 10**8  # Aave USD-based oracles: 8 decimals


@dataclass(frozen=True)
class LiquidationRules:
    """Per-deployment liquidation parameters. Defaults are Aave v3.3 (current Base/Arbitrum pools).
    v3.0-3.2 deployments use min_leftover_base=0 and min_base_max_liquidation_threshold=0."""
    version: str = "3.3"
    close_factor_hf_threshold_wad: int = 95 * WAD // 100   # HF below this -> 100% close factor
    default_close_factor_bps: int = 5_000                  # otherwise 50%
    max_close_factor_bps: int = 10_000
    min_leftover_base: int = 1_000 * BASE_UNIT             # v3.3: remaining debt/collateral must be 0 or >= $1000
    min_base_max_liquidation_threshold: int = 2_000 * BASE_UNIT  # v3.3: positions under $2000 may be fully liquidated


@dataclass(frozen=True)
class ReserveParams:
    address: str
    symbol: str
    decimals: int
    price_raw: int                   # oracle price, 8 decimals
    liquidation_bonus_bps: int       # 10000 + bonus, e.g. 10500
    liquidation_protocol_fee_bps: int
    liquidation_threshold_bps: int
    active: bool = True
    paused: bool = False


@dataclass(frozen=True)
class PositionInputs:
    health_factor_wad: int
    total_collateral_base: int
    total_debt_base: int
    debt: ReserveParams
    collateral: ReserveParams
    user_debt_raw: int               # borrower's debt in the chosen debt reserve (variable + stable)
    user_collateral_raw: int         # borrower's aToken balance in the chosen collateral reserve
    collateral_enabled: bool
    emode_bonus_bps: int | None = None   # effective bonus if the user's e-mode category applies to this collateral


@dataclass(frozen=True)
class CostInputs:
    flash_premium_total_bps: int | None
    gas_units: int | None
    gas_price_wei: int | None
    native_price_raw: int | None     # ETH price, 8 decimals
    l1_data_fee_wei: int | None = None   # L2s: explicit L1 component; None = unknown
    extra_cost_usd: Decimal = Decimal(0)  # builder / bundle costs if any
    swap_out_raw: int | None = None  # debt-token amount received for the seized collateral (after fees), from a quote
    swap_source: str | None = None
    swap_impact_bps: int | None = None
    revert_gas_fraction: Decimal = Decimal("0.35")  # share of gas burnt by a failed attempt (estimate; labeled)


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    close_factor_bps: int = 0
    max_debt_to_cover_raw: int = 0


@dataclass
class LiquidationOutcome:
    debt_to_cover_raw: int
    collateral_seized_raw: int      # total taken from the borrower (before protocol fee)
    protocol_fee_raw: int
    collateral_to_liquidator_raw: int
    effective_bonus_bps: int
    capped_by_collateral: bool
    assumptions: list[str] = field(default_factory=list)


@dataclass
class ProfitReport:
    outcome: LiquidationOutcome
    flash_premium_raw: int | None
    gas_cost_usd: Decimal | None
    gross_bonus_usd: Decimal
    debt_to_cover_usd: Decimal
    collateral_value_usd: Decimal
    swap_out_usd: Decimal | None
    net_profit_debt_raw: int | None
    net_profit_usd: Decimal | None
    return_on_notional: Decimal | None
    breakeven_gas_usd: Decimal | None
    breakeven_slippage_bps: int | None
    max_rational_bid_usd: Decimal | None
    failed_attempt_cost_usd: Decimal | None
    sensitivity: dict[str, str]
    incomplete_reasons: list[str]
    breakdown: dict[str, str]

    @property
    def complete(self) -> bool:
        return not self.incomplete_reasons


# --- step 1-4: eligibility and permitted repayment -------------------------------------------------
def check_eligibility(p: PositionInputs, rules: LiquidationRules) -> Eligibility:
    e = Eligibility(eligible=False)
    if p.health_factor_wad >= WAD:
        e.reasons.append("health factor >= 1")
    if p.user_debt_raw <= 0:
        e.reasons.append("no debt in chosen debt reserve")
    if p.user_collateral_raw <= 0 or not p.collateral_enabled:
        e.reasons.append("no enabled collateral in chosen collateral reserve")
    if p.collateral.liquidation_threshold_bps == 0:
        e.reasons.append("collateral reserve has liquidation threshold 0 (not liquidatable)")
    if not p.debt.active or p.debt.paused or not p.collateral.active or p.collateral.paused:
        e.reasons.append("reserve inactive or paused")
    if e.reasons:
        return e
    cf = rules.default_close_factor_bps
    if p.health_factor_wad < rules.close_factor_hf_threshold_wad:
        cf = rules.max_close_factor_bps
    elif rules.min_base_max_liquidation_threshold and (
        p.total_debt_base < rules.min_base_max_liquidation_threshold
        or p.total_collateral_base < rules.min_base_max_liquidation_threshold
    ):
        cf = rules.max_close_factor_bps  # v3.3: small positions can be closed fully
    e.close_factor_bps = cf
    e.max_debt_to_cover_raw = percent_mul(p.user_debt_raw, cf)
    e.eligible = True
    return e


# --- step 5: collateral received, protocol fee ----------------------------------------------------
def collateral_for_debt(p: PositionInputs, debt_to_cover_raw: int, bonus_bps: int) -> int:
    debt_unit, coll_unit = 10**p.debt.decimals, 10**p.collateral.decimals
    base = (debt_to_cover_raw * p.debt.price_raw * coll_unit) // (p.collateral.price_raw * debt_unit)
    return percent_mul(base, bonus_bps)


def debt_for_collateral(p: PositionInputs, collateral_raw: int, bonus_bps: int) -> int:
    debt_unit, coll_unit = 10**p.debt.decimals, 10**p.collateral.decimals
    base = (collateral_raw * p.collateral.price_raw * debt_unit) // (p.debt.price_raw * coll_unit)
    return percent_div(base, bonus_bps)


def apply_liquidation(p: PositionInputs, rules: LiquidationRules, debt_to_cover_raw: int) -> LiquidationOutcome:
    """Mirror LiquidationLogic._calculateAvailableCollateralToLiquidate plus the v3.3 dust checks."""
    bonus = p.emode_bonus_bps or p.collateral.liquidation_bonus_bps
    assumptions = []
    if p.emode_bonus_bps is None:
        assumptions.append("liquidation bonus taken from reserve config; e-mode override not applied")
    coll = collateral_for_debt(p, debt_to_cover_raw, bonus)
    capped = False
    if coll > p.user_collateral_raw:
        coll = p.user_collateral_raw
        debt_to_cover_raw = debt_for_collateral(p, coll, bonus)
        capped = True
    bonus_collateral = coll - percent_div(coll, bonus)
    fee = percent_mul(bonus_collateral, p.collateral.liquidation_protocol_fee_bps) if p.collateral.liquidation_protocol_fee_bps else 0
    return LiquidationOutcome(debt_to_cover_raw=debt_to_cover_raw, collateral_seized_raw=coll, protocol_fee_raw=fee,
                              collateral_to_liquidator_raw=coll - fee, effective_bonus_bps=bonus, capped_by_collateral=capped,
                              assumptions=assumptions)


def dust_check(p: PositionInputs, rules: LiquidationRules, outcome: LiquidationOutcome) -> list[str]:
    """v3.3 MustNotLeaveDust: remaining debt and remaining collateral (in base currency) must be 0 or >= min_leftover."""
    if not rules.min_leftover_base:
        return []
    problems = []
    rem_debt = p.user_debt_raw - outcome.debt_to_cover_raw
    rem_coll = p.user_collateral_raw - outcome.collateral_seized_raw
    rem_debt_base = rem_debt * p.debt.price_raw // 10**p.debt.decimals
    rem_coll_base = rem_coll * p.collateral.price_raw // 10**p.collateral.decimals
    if 0 < rem_debt_base < rules.min_leftover_base:
        problems.append(f"would leave debt dust ({rem_debt_base / BASE_UNIT:.2f} USD < min leftover); full repayment required")
    if 0 < rem_coll_base < rules.min_leftover_base:
        problems.append(f"would leave collateral dust ({rem_coll_base / BASE_UNIT:.2f} USD < min leftover)")
    return problems


def choose_debt_to_cover(p: PositionInputs, rules: LiquidationRules, elig: Eligibility,
                         max_flash_loan_raw: int | None = None) -> tuple[LiquidationOutcome, list[str]]:
    """Largest permitted repayment that satisfies the dust rules and the flash-loan size limit."""
    target = elig.max_debt_to_cover_raw
    if max_flash_loan_raw is not None:
        target = min(target, max_flash_loan_raw)
    outcome = apply_liquidation(p, rules, target)
    problems = dust_check(p, rules, outcome)
    if problems and elig.close_factor_bps == rules.max_close_factor_bps and target < p.user_debt_raw:
        # try full repayment (allowed when close factor is 100%)
        full = apply_liquidation(p, rules, p.user_debt_raw)
        if not dust_check(p, rules, full) and (max_flash_loan_raw is None or full.debt_to_cover_raw <= max_flash_loan_raw):
            return full, []
    return outcome, problems


# --- steps 7-10: costs and profit ----------------------------------------------------------------
def flash_premium(amount_raw: int, premium_bps: int) -> int:
    return percent_mul(amount_raw, premium_bps)


def gas_cost_usd(c: CostInputs) -> tuple[Decimal | None, list[str]]:
    missing = [n for n, v in (("gas_units", c.gas_units), ("gas_price_wei", c.gas_price_wei),
                              ("native_price", c.native_price_raw)) if v is None]
    if missing:
        return None, [f"gas cost unknown: missing {', '.join(missing)}"]
    wei = c.gas_units * c.gas_price_wei + (c.l1_data_fee_wei or 0)
    usd = raw_to_usd(wei, 18, c.native_price_raw)
    notes = [] if c.l1_data_fee_wei is not None else ["L1 data fee not included in gas cost (unknown)"]
    return quantize_usd(usd), notes


def profit_report(p: PositionInputs, rules: LiquidationRules, outcome: LiquidationOutcome, c: CostInputs,
                  prior_incomplete: list[str] | None = None) -> ProfitReport:
    inc: list[str] = list(prior_incomplete or [])
    d, k = p.debt, p.collateral
    debt_usd = raw_to_usd(outcome.debt_to_cover_raw, d.decimals, d.price_raw)
    coll_usd = raw_to_usd(outcome.collateral_to_liquidator_raw, k.decimals, k.price_raw)
    gross_bonus_usd = coll_usd - debt_usd  # oracle-valued gain before swap, premium and gas
    premium = flash_premium(outcome.debt_to_cover_raw, c.flash_premium_total_bps) if c.flash_premium_total_bps is not None else None
    if premium is None:
        inc.append("flash-loan premium unknown (FLASHLOAN_PREMIUM_TOTAL not read)")
    gas_usd, gas_notes = gas_cost_usd(c)
    inc += gas_notes if gas_usd is None else []
    assumptions = list(outcome.assumptions) + ([n for n in gas_notes if gas_usd is not None])
    swap_out_usd = None if c.swap_out_raw is None else raw_to_usd(c.swap_out_raw, d.decimals, d.price_raw)
    if c.swap_out_raw is None:
        inc.append("no swap quote: collateral-to-debt proceeds unknown")
    net_raw = net_usd = ron = be_gas = be_slip = max_bid = fail_cost = None
    if c.swap_out_raw is not None and premium is not None and gas_usd is not None:
        gas_in_debt_raw = int((gas_usd + c.extra_cost_usd) * Decimal(10**d.decimals) * Decimal(BASE_UNIT) / Decimal(d.price_raw))
        net_raw = c.swap_out_raw - outcome.debt_to_cover_raw - premium - gas_in_debt_raw
        net_usd = quantize_usd(raw_to_usd(net_raw, d.decimals, d.price_raw)) if net_raw >= 0 else \
            -quantize_usd(raw_to_usd(-net_raw, d.decimals, d.price_raw))
        ron = (Decimal(net_raw) / Decimal(outcome.debt_to_cover_raw)).quantize(Decimal("0.0000000001")) if outcome.debt_to_cover_raw else None
        margin_raw = c.swap_out_raw - outcome.debt_to_cover_raw - premium  # before execution costs
        be_gas = quantize_usd(raw_to_usd(max(margin_raw, 0), d.decimals, d.price_raw))
        be_slip = int(max(net_raw, 0) * BPS // c.swap_out_raw) if c.swap_out_raw else None
        max_bid = be_gas  # the most one could rationally spend on execution in total
        fail_cost = quantize_usd(gas_usd * c.revert_gas_fraction)
    sens = {}
    for label, shock_bps in (("collateral_-1%", -100), ("collateral_-5%", -500), ("collateral_+1%", 100)):
        if c.swap_out_raw is not None and net_raw is not None:
            shocked_out = c.swap_out_raw * (BPS + shock_bps) // BPS
            sens[label] = str(quantize_usd(raw_to_usd(shocked_out - (c.swap_out_raw - net_raw), d.decimals, d.price_raw)))
    breakdown = {
        "debt_to_cover": str(outcome.debt_to_cover_raw), "debt_to_cover_usd": str(quantize_usd(debt_usd)),
        "collateral_seized": str(outcome.collateral_seized_raw), "protocol_fee_collateral": str(outcome.protocol_fee_raw),
        "collateral_to_liquidator": str(outcome.collateral_to_liquidator_raw), "collateral_value_usd": str(quantize_usd(coll_usd)),
        "effective_bonus_bps": str(outcome.effective_bonus_bps), "flash_premium": "" if premium is None else str(premium),
        "swap_out": "" if c.swap_out_raw is None else str(c.swap_out_raw), "swap_source": c.swap_source or "",
        "swap_impact_bps": "" if c.swap_impact_bps is None else str(c.swap_impact_bps),
        "gas_cost_usd": "" if gas_usd is None else str(gas_usd), "extra_cost_usd": str(c.extra_cost_usd),
        "net_profit_debt_raw": "" if net_raw is None else str(net_raw), "assumptions": "; ".join(assumptions),
        "rules_version": rules.version,
    }
    return ProfitReport(outcome=outcome, flash_premium_raw=premium, gas_cost_usd=gas_usd, gross_bonus_usd=quantize_usd(gross_bonus_usd),
                        debt_to_cover_usd=quantize_usd(debt_usd), collateral_value_usd=quantize_usd(coll_usd), swap_out_usd=swap_out_usd,
                        net_profit_debt_raw=net_raw, net_profit_usd=net_usd, return_on_notional=ron, breakeven_gas_usd=be_gas,
                        breakeven_slippage_bps=be_slip, max_rational_bid_usd=max_bid, failed_attempt_cost_usd=fail_cost,
                        sensitivity=sens, incomplete_reasons=inc, breakdown=breakdown)


def trigger_price(p: PositionInputs) -> Decimal | None:
    """Approximate collateral price at which HF hits 1, for a single-collateral position:
    HF = coll_value * LT / debt_value  ->  price* = debt_base * BPS / (coll_units * LT).  Prediction only."""
    if p.user_collateral_raw <= 0 or p.total_debt_base <= 0 or p.collateral.liquidation_threshold_bps == 0:
        return None
    coll_units = Decimal(p.user_collateral_raw) / Decimal(10**p.collateral.decimals)
    debt_usd = Decimal(p.total_debt_base) / Decimal(BASE_UNIT)
    return (debt_usd * BPS / (coll_units * p.collateral.liquidation_threshold_bps)).quantize(Decimal("0.00000001"))
