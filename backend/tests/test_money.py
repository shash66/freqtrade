from decimal import Decimal

import pytest

from app.core.money import WAD, health_factor, percent_mul, raw_to_usd, to_raw, to_units, usd_to_raw


def test_units_roundtrip_exact():
    assert to_units(1_234_567, 6) == Decimal("1.234567")
    assert to_raw(Decimal("1.234567"), 6) == 1_234_567
    assert to_raw("0.1", 18) == 10**17  # no float drift


def test_to_raw_truncates_not_rounds():
    assert to_raw(Decimal("1.9999999"), 6) == 1_999_999


def test_usd_conversion_uses_8_decimal_oracle():
    # 2 WETH at $2,500.00 -> $5,000
    assert raw_to_usd(2 * 10**18, 18, 2_500 * 10**8) == Decimal(5000)
    assert usd_to_raw(Decimal(5000), 18, 2_500 * 10**8) == 2 * 10**18


def test_percent_mul_matches_aave_rounding():
    # Aave: (value * bps + 5000) / 10000
    assert percent_mul(10_000, 10_500) == 10_500  # 5% bonus
    assert percent_mul(3, 5_000) == 2  # 1.5 rounds half up to 2


def test_health_factor_infinite_without_debt():
    assert health_factor(2**256 - 1) == Decimal("Infinity")
    assert health_factor(WAD) == Decimal(1)
    assert health_factor(105 * WAD // 100) == Decimal("1.05")


def test_rejects_float_raw():
    with pytest.raises(TypeError):
        to_units(1.0, 6)  # type: ignore[arg-type]
