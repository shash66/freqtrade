from app.validation.phase2 import summarize_replays


def _r(tx, success, profit, complete, attempts=1, ef=True, ee=True, path="flash_loan_receiver"):
    return {"tx": tx, "success": success, "net_profit_usd": profit, "profit_complete": complete, "attempts": attempts,
            "eligible_at_fork": ef, "eligible_at_execution": ee, "execution_path": path, "fees": {"source": "verified"}}


def test_retries_do_not_inflate_events_or_successes():
    rows = [_r("0xa", True, "3120.28", True, attempts=3), _r("0xa", True, "0.43", True),          # two events in one tx
            _r("0xb", False, None, None, attempts=2, ef=True, ee=False),
            _r("0xc", True, "5.00", False)]                                                        # cost unknown -> never "profitable"
    d = summarize_replays(rows, "t")
    assert d["unique_historical_txs"] == 3 and d["liquidation_events"] == 4 and d["simulation_attempts"] == 7
    assert d["events_with_retry"] == 2 and d["succeeded"] == 3 and d["reverted"] == 1
    assert d["profitable"] == 2 and d["succeeded_with_incomplete_costs"] == 1
    assert d["net_profit_usd_total_complete"] == "3120.71"


def test_eligibility_tracked_at_fork_and_execution_separately():
    rows = [_r("0xa", True, "1", True, ef=False, ee=True), _r("0xb", False, None, None, ef=False, ee=False), _r("0xc", True, "1", True)]
    d = summarize_replays(rows, "t")
    assert d["not_eligible_at_block_boundary"] == 2 and d["eligible_at_execution_only"] == 1
    assert d["eligibility"] == {"fork=False,execution=True": 1, "fork=False,execution=False": 1, "fork=True,execution=True": 1}
