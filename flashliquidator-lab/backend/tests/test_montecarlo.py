from app.competition.montecarlo import delay_sensitivity, simulate
from app.competition.strategies import COMPETITOR_DEFAULTS, STRATEGIES


def test_reproducible_with_seed():
    a = simulate(STRATEGIES["B"]["parameters"], dict(COMPETITOR_DEFAULTS, trials=2000), seed=7)
    b = simulate(STRATEGIES["B"]["parameters"], dict(COMPETITOR_DEFAULTS, trials=2000), seed=7)
    assert a.win_rate == b.win_rate and a.expected_profit_per_attempt_usd == b.expected_profit_per_attempt_usd


def test_faster_strategy_wins_more_and_intervals_are_sane():
    comp = dict(COMPETITOR_DEFAULTS, trials=4000)
    ra, rb, rc = (simulate(STRATEGIES[k]["parameters"], comp, 1) for k in "ABC")
    assert ra.win_rate <= rb.win_rate <= rc.win_rate and rc.win_rate > ra.win_rate + 0.05
    for r in (ra, rb, rc):
        lo, hi = r.win_rate_ci95
        assert 0 <= lo <= r.win_rate <= hi <= 1
        assert r.label.startswith("modeled")
        assert abs(r.attempts_rate - (r.win_rate + r.failed_attempt_rate)) < 1e-9


def test_polling_rarely_beats_fast_competitors():
    r = simulate(STRATEGIES["A"]["parameters"], dict(COMPETITOR_DEFAULTS, trials=3000), 3)
    assert r.win_rate < 0.05


def test_delay_sensitivity_is_monotone_nonincreasing():
    rows = delay_sensitivity(STRATEGIES["C"]["parameters"], dict(COMPETITOR_DEFAULTS, trials=2000), 5)
    wins = [r["win_rate"] for r in rows]
    assert all(wins[i] >= wins[i + 1] - 0.02 for i in range(len(wins) - 1))


def test_priority_auction_model_runs():
    r = simulate(STRATEGIES["C"]["parameters"], dict(COMPETITOR_DEFAULTS, trials=500, inclusion_model="priority_auction"), 9)
    assert 0 <= r.win_rate <= 1


def test_per_attempt_profit_excludes_expired_trials():
    comp = dict(COMPETITOR_DEFAULTS, trials=3000, candidate_expiry_per_second=5.0)  # most opportunities vanish first
    r = simulate(STRATEGIES["C"]["parameters"], comp, 11)
    assert r.attempts_rate < 0.5
    # per-trial mean is diluted by zeros; per-attempt mean is not
    assert abs(r.expected_profit_per_trial_usd) <= abs(r.expected_profit_per_attempt_usd) + 1e-9
    r0 = simulate(STRATEGIES["C"]["parameters"], dict(COMPETITOR_DEFAULTS, trials=200, n_competitors=0), 1)
    assert r0.win_rate > 0.9 and r0.mean_competitor_ready_ms == 0.0
