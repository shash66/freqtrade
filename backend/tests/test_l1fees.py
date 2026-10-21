from app.chains.l1fees import FeeAccounting, serialize_unsigned_1559, verified_fees_from_receipt


def test_serialized_tx_has_realistic_size():
    raw = serialize_unsigned_1559(8453, "0x" + "a" * 40, b"\x00" * 100, 500_000, 10**9)
    assert raw[0] == 2 and 100 + 64 + 20 < len(raw) < 100 + 64 + 20 + 60


def test_verified_base_fee_from_receipt():
    f = verified_fees_from_receipt(8453, {"gasUsed": "0x30d40", "effectiveGasPrice": "0x3b9aca00", "l1Fee": "0x5f5e100"})
    assert f.source == "verified" and f.l2_execution_wei == 200_000 * 10**9 and f.l1_data_wei == 100_000_000
    assert f.total_wei == 200_000 * 10**9 + 100_000_000


def test_verified_arbitrum_fee_separates_l1_component_without_double_counting():
    f = verified_fees_from_receipt(42161, {"gasUsed": 1_000_000, "effectiveGasPrice": 10**7, "gasUsedForL1": 300_000})
    assert f.source == "verified" and f.l1_data_wei == 300_000 * 10**7 and f.l2_execution_wei == 700_000 * 10**7
    assert f.total_wei == 1_000_000 * 10**7


def test_missing_receipt_fields_are_unknown_not_zero():
    f = verified_fees_from_receipt(8453, {"gasUsed": 1, "effectiveGasPrice": 1})
    assert f.source == "unknown" and f.l1_data_wei is None and f.total_wei is None
    assert FeeAccounting(5, None, "not_applicable", "x").total_wei == 5
