"""The three baseline strategies (spec §9) as parameter sets for the detection/execution model.
Delays are modeled assumptions, labeled as such in every output; they are not measured latencies."""
from __future__ import annotations

STRATEGIES: dict[str, dict] = {
    "A": {
        "name": "Basic polling",
        "description": "Periodic health-factor checks over the tracked borrower set; standard gas estimate; no prediction.",
        "parameters": {
            "poll_interval_ms": 30_000, "detection_delay_mean_ms": 15_000, "detection_delay_sd_ms": 8_000,
            "simulation_time_ms": 800, "rpc_delay_ms": 300, "revalidate_ms": 0, "gas_bid_multiplier": 1.0,
            "precomputed_routes": False,
        },
    },
    "B": {
        "name": "Event-driven",
        "description": "WebSocket subscriptions to pool and oracle events; immediate simulation after relevant events; prioritized RPC.",
        "parameters": {
            "poll_interval_ms": 0, "detection_delay_mean_ms": 400, "detection_delay_sd_ms": 200,
            "simulation_time_ms": 600, "rpc_delay_ms": 150, "revalidate_ms": 0, "gas_bid_multiplier": 1.1,
            "precomputed_routes": False,
        },
    },
    "C": {
        "name": "Predictive optimized",
        "description": "Tracks near-liquidation borrowers with trigger-price sensitivities, precomputes debt/collateral pairs and cached swap routes, revalidates before simulating, sizes the transaction for cost.",
        "parameters": {
            "poll_interval_ms": 0, "detection_delay_mean_ms": 150, "detection_delay_sd_ms": 80,
            "simulation_time_ms": 200, "rpc_delay_ms": 100, "revalidate_ms": 100, "gas_bid_multiplier": 1.2,
            "precomputed_routes": True,
        },
    },
}

COMPETITOR_DEFAULTS = {
    "n_competitors": 5, "competitor_delay_mean_ms": 700, "competitor_delay_sd_ms": 350,   # time-to-ready of each competitor (detect+simulate+send); an assumption, adjust in the Competition Lab
    "candidate_expiry_per_second": 0.02,   # probability per second that the opportunity disappears (price move / repay)
    "inclusion_model": "fcfs",              # fcfs (L2 sequencers) | priority_auction (Ethereum builders)
    "competitor_gas_bid_gwei_mean": 0.05, "competitor_gas_bid_gwei_sd": 0.03,
    "expected_profit_usd": 25.0,            # overridden by the replay dataset median when available
    "failed_attempt_cost_usd": 0.05,        # gas burnt by a reverted attempt (L2 figures are small; label as assumption)
    "trials": 5_000,
}
