"""Exact arithmetic helpers. Token amounts are ints in base units; USD values are Decimal. No floats."""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, getcontext

getcontext().prec = 60

RAY = 10**27
WAD = 10**18
BPS = 10_000
USD_ORACLE_DECIMALS = 8  # Aave V3 oracles return prices with 8 decimals (USD-based markets)


def to_units(raw: int, decimals: int) -> Decimal:
    """Base units -> human units as an exact Decimal."""
    if not isinstance(raw, int):
        raise TypeError("raw amount must be int")
    return Decimal(raw).scaleb(-decimals)


def to_raw(amount: Decimal | str | int, decimals: int) -> int:
    """Human units -> base units, truncating toward zero (never rounding up a balance)."""
    d = Decimal(amount)
    return int(d.scaleb(decimals).to_integral_value(rounding=ROUND_DOWN))


def raw_to_usd(raw: int, decimals: int, price_raw: int, price_decimals: int = USD_ORACLE_DECIMALS) -> Decimal:
    return to_units(raw, decimals) * Decimal(price_raw).scaleb(-price_decimals)


def usd_to_raw(usd: Decimal, decimals: int, price_raw: int, price_decimals: int = USD_ORACLE_DECIMALS) -> int:
    price = Decimal(price_raw).scaleb(-price_decimals)
    if price <= 0:
        raise ValueError("price must be positive")
    return to_raw(usd / price, decimals)


def percent_mul(raw: int, bps: int) -> int:
    """Aave PercentageMath.percentMul: rounds half up, as the protocol does."""
    return (raw * bps + BPS // 2) // BPS


def percent_div(raw: int, bps: int) -> int:
    return (raw * BPS + bps // 2) // bps


def wad_div(a: int, b: int) -> int:
    return (a * WAD + b // 2) // b


def ray_mul(a: int, b: int) -> int:
    return (a * b + RAY // 2) // RAY


def health_factor(hf_wad: int) -> Decimal:
    """Aave returns health factor as WAD; type(uint256).max means no debt."""
    if hf_wad >= 2**256 - 1:
        return Decimal("Infinity")
    return Decimal(hf_wad) / Decimal(WAD)


def quantize_usd(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
