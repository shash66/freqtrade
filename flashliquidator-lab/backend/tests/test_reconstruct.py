from web3 import Web3

from app.simulation.reconstruct import CHAINLINK_TOPICS, classify_tx, pool_event_topics

POOL = "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
BORROWER = "0x66bb6c2949b20503ADF3621db9903Ee52F92ffB5"
TOPICS = pool_event_topics()
LIQ = next(k for k, v in TOPICS.items() if v == "LiquidationCall")
BORROW = next(k for k, v in TOPICS.items() if v == "Borrow")
ANSWER = next(k for k, v in CHAINLINK_TOPICS.items() if v == "AnswerUpdated")


def _pad(addr: str) -> str:
    return "0x" + addr[2:].lower().rjust(64, "0")


def test_pool_event_topics_match_known_signature():
    expected = Web3.keccak(text="LiquidationCall(address,address,address,uint256,uint256,address,bool)").hex()
    assert LIQ[-64:] == expected[-64:] and LIQ.startswith("0x")


def test_oracle_update_is_classified_from_chainlink_logs():
    kind, ev = classify_tx([{"address": "0xfeed", "topics": [ANSWER, "0x01", "0x02"]}], POOL, BORROWER, TOPICS)
    assert kind == "oracle_update" and ev == ["AnswerUpdated from 0xfeed"]


def test_borrower_liquidation_outranks_oracle_update_in_same_tx():
    logs = [{"address": "0xfeed", "topics": [ANSWER, "0x01", "0x02"]},
            {"address": POOL.lower(), "topics": [LIQ, _pad("0x4200000000000000000000000000000000000006"), _pad("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"), _pad(BORROWER)]}]
    kind, ev = classify_tx(logs, POOL, BORROWER, TOPICS)
    assert kind == "liquidation_of_borrower" and "Pool.LiquidationCall (borrower)" in ev


def test_other_users_pool_activity_and_unrelated_txs():
    other = _pad("0x1234567890123456789012345678901234567890")
    assert classify_tx([{"address": POOL, "topics": [BORROW, other, other]}], POOL, BORROWER, TOPICS)[0] == "pool_activity"
    assert classify_tx([{"address": POOL, "topics": [BORROW, other, _pad(BORROWER)]}], POOL, BORROWER, TOPICS)[0] == "borrower_action"
    assert classify_tx([{"address": "0xabc", "topics": ["0x" + "ee" * 32]}], POOL, BORROWER, TOPICS) == ("other", [])
    assert classify_tx([], POOL, BORROWER, TOPICS) == ("other", [])
