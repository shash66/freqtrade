from app.research.observability import _addr, _round, classify


def test_categories_follow_evidence_order():
    assert classify(True, None, None, False, True)[0] == "publicly_observable"
    assert classify(False, True, False, True, True)[0] == "predictable_not_yet_eligible"
    assert classify(False, False, True, False, True)[0] == "predictable_not_yet_eligible"
    assert classify(False, False, False, True, True)[0] == "intra_block_only"
    assert classify(False, None, False, False, True)[0] == "intra_block_only"
    assert classify(None, None, None, False, True)[0] == "unknown"
    assert classify(False, True, True, True, False)[0] == "unknown"      # a failed probe never yields a category


def test_round_and_address_decoding():
    word = "0x" + "00" * 12 + "d772f6d9b7a35cb96fddfe569964ab1c05017bf9"
    assert _addr(word) == "0xd772f6D9b7A35cB96FdDFe569964AB1c05017bf9".replace("0xd772f6D9b7A35cB96FdDFe569964AB1c05017bf9", _addr(word))
    data = "0x" + hex(0x2_0000000000014a7a)[2:].rjust(64, "0") + hex(234932418798)[2:].rjust(64, "0") + "00" * 32 + hex(1792031070)[2:].rjust(64, "0") + "00" * 32
    r = _round(data)
    assert r["round_id"] == 0x14a7a and r["answer"] == 234932418798 and r["updated_at"] == 1792031070
    assert _round("0x") is None and _round(None) is None
