"""Seeded Monte Carlo of competing liquidation submissions (spec §9). Outputs are modeled estimates with
confidence intervals; a 'win rate' here is never a measured execution win rate."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class McResult:
    trials: int
    seed: int
    win_rate: float
    win_rate_ci95: tuple[float, float]
    expired_rate: float
    expected_profit_per_attempt_usd: float
    expected_profit_ci95: tuple[float, float]
    attempts_rate: float
    failed_attempt_rate: float
    mean_our_ready_ms: float
    mean_competitor_ready_ms: float
    profit_distribution: dict[str, float]
    assumptions: list[str] = field(default_factory=list)
    label: str = "modeled estimate, not measured execution performance"


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _gauss_pos(rng: random.Random, mean: float, sd: float) -> float:
    return max(0.0, rng.gauss(mean, sd))


def simulate(strategy_params: dict, competition: dict, seed: int = 42) -> McResult:
    rng = random.Random(seed)
    trials = int(competition.get("trials", 5000))
    n_comp = int(competition.get("n_competitors", 5))
    exp_profit = float(competition.get("expected_profit_usd", 25.0))
    fail_cost = float(competition.get("failed_attempt_cost_usd", 0.05))
    expiry_ps = float(competition.get("candidate_expiry_per_second", 0.02))
    model = competition.get("inclusion_model", "fcfs")
    our_bid_mult = float(strategy_params.get("gas_bid_multiplier", 1.0))
    wins = expired = attempts = failed = 0
    profits: list[float] = []
    ours_ms: list[float] = []
    comps_ms: list[float] = []
    for _ in range(trials):
        ours = (_gauss_pos(rng, strategy_params["detection_delay_mean_ms"], strategy_params["detection_delay_sd_ms"])
                + strategy_params["simulation_time_ms"] + strategy_params["rpc_delay_ms"] + strategy_params.get("revalidate_ms", 0))
        comp_times = [_gauss_pos(rng, competition["competitor_delay_mean_ms"], competition["competitor_delay_sd_ms"]) for _ in range(n_comp)]
        best_comp = min(comp_times) if comp_times else math.inf
        ours_ms.append(ours)
        comps_ms.append(best_comp if best_comp != math.inf else 0.0)
        # opportunity lifetime (exponential), in ms
        life = rng.expovariate(expiry_ps) * 1000 if expiry_ps > 0 else math.inf
        if ours > life and best_comp > life:
            expired += 1
            profits.append(0.0)
            continue
        if ours > life:  # someone else or nobody got it before it vanished; we never submitted
            profits.append(0.0)
            continue
        attempts += 1
        if model == "priority_auction":
            # same block window: highest bid wins among those ready within the block; ties broken by time
            our_bid = competition["competitor_gas_bid_gwei_mean"] * our_bid_mult
            comp_bids = [_gauss_pos(rng, competition["competitor_gas_bid_gwei_mean"], competition["competitor_gas_bid_gwei_sd"])
                         for t in comp_times if t <= life]
            won = all(our_bid > b for b in comp_bids) or (not comp_bids)
        else:  # fcfs: earliest arrival at the sequencer
            won = ours < best_comp
        if won:
            wins += 1
            profits.append(exp_profit)
        else:
            failed += 1
            profits.append(-fail_cost)
    n = trials
    mean_p = sum(profits) / n
    var = sum((p - mean_p) ** 2 for p in profits) / max(n - 1, 1)
    se = math.sqrt(var / n)
    sp = sorted(profits)
    dist = {"p10": sp[n // 10], "p50": sp[n // 2], "p90": sp[(9 * n) // 10], "min": sp[0], "max": sp[-1]}
    return McResult(
        trials=n, seed=seed, win_rate=wins / n, win_rate_ci95=_wilson(wins, n), expired_rate=expired / n,
        expected_profit_per_attempt_usd=mean_p, expected_profit_ci95=(mean_p - 1.96 * se, mean_p + 1.96 * se),
        attempts_rate=attempts / n, failed_attempt_rate=failed / n,
        mean_our_ready_ms=sum(ours_ms) / n, mean_competitor_ready_ms=sum(comps_ms) / n, profit_distribution=dist,
        assumptions=[
            f"{n_comp} competitors with detection delay ~N({competition['competitor_delay_mean_ms']} ms, {competition['competitor_delay_sd_ms']} ms)",
            f"inclusion model: {model}",
            f"opportunity lifetime exponential with rate {expiry_ps}/s",
            f"profit per won attempt fixed at {exp_profit} USD; failed attempt costs {fail_cost} USD",
            "block timestamps and mempool arrival are not used as latency measurements",
        ])


def delay_sensitivity(strategy_params: dict, competition: dict, seed: int, extra_delays_ms=(0, 100, 250, 500, 1000, 2000)) -> list[dict]:
    rows = []
    for d in extra_delays_ms:
        p = dict(strategy_params, detection_delay_mean_ms=strategy_params["detection_delay_mean_ms"] + d)
        r = simulate(p, dict(competition, trials=min(int(competition.get("trials", 5000)), 2000)), seed)
        rows.append({"extra_delay_ms": d, "win_rate": r.win_rate, "expected_profit_per_attempt_usd": r.expected_profit_per_attempt_usd})
    return rows
