from app.aave.reads import ReserveConfig


def _bitmap(**fields) -> int:
    layout = {"ltv": (0, 16), "lt": (16, 16), "bonus": (32, 16), "decimals": (48, 8), "active": (56, 1), "frozen": (57, 1),
              "borrowing": (58, 1), "paused": (60, 1), "reserve_factor": (64, 16), "borrow_cap": (80, 36), "supply_cap": (116, 36),
              "protocol_fee": (152, 16), "emode": (168, 8), "debt_ceiling": (212, 40), "virtual": (252, 1)}
    data = 0
    for k, v in fields.items():
        start, width = layout[k]
        assert v < (1 << width)
        data |= v << start
    return data


def test_reserve_config_bitmap_decodes_every_field():
    data = _bitmap(ltv=8000, lt=8250, bonus=10500, decimals=18, active=1, frozen=0, borrowing=1, paused=0,
                   reserve_factor=1500, borrow_cap=100_000, supply_cap=250_000, protocol_fee=1000, emode=1,
                   debt_ceiling=0, virtual=1)
    c = ReserveConfig.from_bitmap(data)
    assert (c.ltv_bps, c.liquidation_threshold_bps, c.liquidation_bonus_bps, c.decimals) == (8000, 8250, 10500, 18)
    assert c.active and not c.frozen and c.borrowing_enabled and not c.paused
    assert c.reserve_factor_bps == 1500 and c.borrow_cap == 100_000 and c.supply_cap == 250_000
    assert c.liquidation_protocol_fee_bps == 1000 and c.emode_category_legacy == 1 and c.virtual_accounting_active


def test_reserve_config_zero_means_not_collateral():
    c = ReserveConfig.from_bitmap(0)
    assert c.liquidation_threshold_bps == 0 and not c.active
