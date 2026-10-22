from app.competition.bidmodel import (
    empirical_cdf,
    n_ready_from_concentration,
    p_win_fcfs,
    p_win_priority_auction,
)


def test_empirical_cdf_counts_strictly_lower_samples():
    assert empirical_cdf([1, 2, 3, 4], 3) == 0.5          # ties are losses
    assert empirical_cdf([], 3) == 0.0
    assert empirical_cdf([1, 2], 10) == 1.0


def test_priority_auction_win_probability_compounds_per_competitor():
    bids = [10, 20, 30, 40, 50]
    assert p_win_priority_auction(45, bids, 1) == 0.8
    assert abs(p_win_priority_auction(45, bids, 2) - 0.64) < 1e-12
    assert p_win_priority_auction(5, bids, 3) == 0.0
    assert p_win_priority_auction(100, bids, 0) == 1.0


def test_fcfs_uses_observed_reactions_and_is_null_free():
    assert p_win_fcfs(1.0, [0.5, 2.0, 4.0]) == 2 / 3
    assert p_win_fcfs(1.0, []) == 0.0


def test_effective_competitors_from_share_concentration():
    assert n_ready_from_concentration([0.46, 0.2, 0.1, 0.04, 0.04]) == 3.0
    assert n_ready_from_concentration([]) == 0.0
