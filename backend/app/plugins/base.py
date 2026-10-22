"""Strategy plugin framework (Phase 5, §2).

A strategy is a self-contained research module that answers the same questions in the same shape, so strategies
of different kinds (liquidations, arbitrage, yield) can be ranked side by side without mixing their evidence:

    discover(ctx)            -> list[Opportunity]        what exists now (or at a historical block)
    state(ctx, opp)          -> dict                     the market/position state the opportunity rests on
    simulate(ctx, opp)       -> SimulationOutcome        the complete transaction on a private fork (or a labeled estimate)
    competition(ctx, opp)    -> CompetitionEstimate      capture probability with its assumptions
    replay(ctx, from, to)    -> ReplayReport             historical evaluation over a block range
    shadow_tick(ctx)         -> ShadowObservation        one live, read-only observation for shadow monitoring

Every number carries its outcome class (`theoretical`, `simulated`, `modeled`, `onchain_actual`, `passive_yield`)
and a data-quality label; a missing input makes a field None with a reason, never an estimate presented as a
measurement. Nothing in a plugin may build, sign or broadcast a public-chain transaction: the only execution
surface is a private Anvil fork through `app.simulation.anvil`.

Strategies are registered by name (`registry.STRATEGIES`), configured from environment variables prefixed by
`STRATEGY_<NAME>_` (see `StrategyConfig`), and tested independently (`tests/test_plugins_*.py`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Protocol

OUTCOME_CLASSES = ("theoretical", "simulated", "modeled", "onchain_actual", "passive_yield")
EVIDENCE = ("historical", "fork", "shadow", "live_read", "modeled")
RISK_LEVELS = ("low", "medium", "high", "unknown")


@dataclass
class StrategyConfig:
    """Common knobs; a plugin may extend this with its own dataclass. Values come from `STRATEGY_<NAME>_<FIELD>`."""
    enabled: bool = True
    networks: tuple[str, ...] = ("base", "arbitrum")
    min_net_profit_usd: Decimal = Decimal("0.05")
    max_capital_usd: Decimal = Decimal("100000")
    max_flash_loan_usd: Decimal = Decimal("100000")
    extra: dict = field(default_factory=dict)


@dataclass
class Context:
    """Everything a plugin needs to run: network config, RPC clients, an optional private fork, block pin."""
    network: object                 # app.chains.config.NetworkConfig
    client: object                  # app.chains.rpc.RpcClient (live or archive)
    archive_client: object | None = None
    anvil_url: str | None = None
    block: int | str = "latest"
    config: StrategyConfig = field(default_factory=StrategyConfig)


@dataclass
class CostBreakdown:
    gas_units: int | None = None
    gas_price_wei: int | None = None
    l2_execution_usd: Decimal | None = None
    l1_data_fee_usd: Decimal | None = None
    flash_premium_usd: Decimal | None = None
    swap_fees_usd: Decimal | None = None          # DEX fees paid inside the route (already inside swap outputs when measured)
    financing_usd: Decimal | None = None          # borrowing cost for capital held (yield strategies)
    other_usd: Decimal | None = None
    source: str = "unknown"                       # estimated | measured | mixed | unknown
    missing: list[str] = field(default_factory=list)

    @property
    def total_usd(self) -> Decimal | None:
        parts = [self.l2_execution_usd, self.l1_data_fee_usd, self.flash_premium_usd, self.financing_usd, self.other_usd]
        if any(p is None for p in parts if p is not None) or self.missing:
            return None
        return sum((p for p in parts if p is not None), Decimal(0))


@dataclass
class Opportunity:
    strategy: str
    network: str
    kind: str                                     # liquidation | arbitrage | yield
    key: str                                      # stable id within the strategy (borrower, pool pair, asset)
    block: int | str
    description: str
    required_capital_usd: Decimal | None          # own capital a taker must hold (0 when fully flash-financed)
    flash_loan_eligible: bool | None
    flash_loan_note: str = ""
    gross_profit_usd: Decimal | None = None       # before every cost, at oracle/quote prices
    costs: CostBreakdown = field(default_factory=CostBreakdown)
    net_profit_usd: Decimal | None = None         # gross minus every cost; None when any material cost is unknown
    net_profit_complete: bool = False
    outcome_class: str = "theoretical"
    evidence: str = "live_read"
    data_quality: str = "unknown"                 # verified | estimated | partial | unknown
    confidence: str = "low"                       # low | medium | high (why: in notes)
    risk: str = "unknown"
    risk_notes: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        d["costs"] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d["costs"].items()}
        d["costs"]["total_usd"] = None if self.costs.total_usd is None else str(self.costs.total_usd)
        return d


@dataclass
class SimulationOutcome:
    success: bool
    outcome_class: str                            # simulated | theoretical (when no fork was available)
    revert_reason: str | None
    gross_profit_usd: Decimal | None
    costs: CostBreakdown
    net_profit_usd: Decimal | None
    net_profit_complete: bool
    measured: dict = field(default_factory=dict)  # raw figures read from the fork (amounts, gas, events)
    fork_block: int | None = None
    path: str = "none"                            # which contract/calls were executed
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("gross_profit_usd", "net_profit_usd"):
            d[k] = None if d[k] is None else str(d[k])
        d["costs"] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d["costs"].items()}
        d["costs"]["total_usd"] = None if self.costs.total_usd is None else str(self.costs.total_usd)
        return d


@dataclass
class CompetitionEstimate:
    p_capture: Decimal | None                     # modeled probability the opportunity is captured by this system
    attainable_net_profit_usd: Decimal | None
    failed_attempt_cost_usd: Decimal | None
    model: str                                    # which model produced it
    assumptions: list[str] = field(default_factory=list)
    observed: dict = field(default_factory=dict)  # measured inputs (competitor counts, bids, lifetimes)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("p_capture", "attainable_net_profit_usd", "failed_attempt_cost_usd"):
            d[k] = None if d[k] is None else str(d[k])
        return d


@dataclass
class ReplayReport:
    strategy: str
    network: str
    from_block: int
    to_block: int
    opportunities: int
    simulated: int
    simulated_success: int
    net_profitable_complete: int
    lost_to_competition: int | None
    expected_profit_per_attempt_usd: Decimal | None
    worst_case_loss_usd: Decimal | None
    sample_note: str
    details: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("expected_profit_per_attempt_usd", "worst_case_loss_usd"):
            d[k] = None if d[k] is None else str(d[k])
        return d


@dataclass
class ShadowObservation:
    strategy: str
    network: str
    block: int
    observed_at: str
    opportunities: list[dict]
    latency_ms: float
    notes: list[str] = field(default_factory=list)


class Strategy(Protocol):
    name: str
    kind: str
    description: str

    def discover(self, ctx: Context) -> list[Opportunity]: ...
    def state(self, ctx: Context, opp: Opportunity) -> dict: ...
    def simulate(self, ctx: Context, opp: Opportunity) -> SimulationOutcome: ...
    def competition(self, ctx: Context, opp: Opportunity, sim: SimulationOutcome | None) -> CompetitionEstimate: ...
    def replay(self, ctx: Context, from_block: int, to_block: int) -> ReplayReport: ...
    def shadow_tick(self, ctx: Context) -> ShadowObservation: ...
