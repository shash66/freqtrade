"""Strategy registry and configuration. Strategies register themselves by name; configuration comes from
environment variables `STRATEGY_<NAME>_<FIELD>` (e.g. STRATEGY_COMPOUND_V3_ENABLED=false,
STRATEGY_DEX_ARB_MIN_NET_PROFIT_USD=0.10). Unknown fields go to `extra` so a plugin can read its own knobs."""
from __future__ import annotations

import os
from decimal import Decimal

from app.plugins.base import Strategy, StrategyConfig

STRATEGIES: dict[str, Strategy] = {}


def register(strategy: Strategy) -> Strategy:
    STRATEGIES[strategy.name] = strategy
    return strategy


def config_for(name: str, defaults: StrategyConfig | None = None) -> StrategyConfig:
    cfg = defaults or StrategyConfig()
    prefix = f"STRATEGY_{name.upper()}_"
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        fld = k[len(prefix):].lower()
        if fld == "enabled":
            cfg.enabled = v.strip().lower() in ("1", "true", "yes", "on")
        elif fld == "networks":
            cfg.networks = tuple(x.strip() for x in v.split(",") if x.strip())
        elif fld in ("min_net_profit_usd", "max_capital_usd", "max_flash_loan_usd"):
            setattr(cfg, fld, Decimal(v))
        else:
            cfg.extra[fld] = v
    return cfg


def load_all() -> dict[str, Strategy]:
    """Import every built-in plugin module so it registers itself."""
    from app.plugins import compound_v3, dex_arb, yield_opt  # noqa: F401  (registration side effects)
    return STRATEGIES
