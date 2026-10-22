from app.simulation.fast_path import FastEvaluation, StageTimings


def test_fast_evaluation_round_trips_and_class():
    e = FastEvaluation("0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40, 1, True, "0.99", [], 1, 1, 1, 500, 1, 1, 1, 1, "estimated", "1", "0.5", True,
                       timings=StageTimings(rpc_reads_ms=100, math_ms=0.1, quote_ms=120, fee_estimate_ms=30, total_ms=260, rpc_round_trips=3))
    d = e.as_dict()
    assert d["outcome_class"] == "theoretical" and d["timings"]["rpc_round_trips"] == 3 and d["complete"] is True
