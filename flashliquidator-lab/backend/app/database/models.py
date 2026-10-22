"""Schema per spec §12. Raw on-chain amounts are NUMERIC(78,0) (uint256 fits); USD values NUMERIC(38,18).
Every chain-scoped row carries chain_id so identifiers never collide across networks."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

RAW = Numeric(78, 0)
USD = Numeric(38, 18)
ADDR = String(42)
HASH = String(66)


class OutcomeClass(enum.StrEnum):
    """The four result classes the spec says must never be mixed."""
    theoretical = "theoretical"
    simulated = "simulated"
    modeled_capturable = "modeled_capturable"
    onchain_actual = "onchain_actual"


class CandidateStatus(enum.StrEnum):
    candidate = "candidate"
    simulated = "simulated"
    invalid = "invalid"
    expired = "expired"
    incomplete = "incomplete"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Network(TimestampMixin, Base):
    __tablename__ = "networks"
    chain_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    native_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    explorer_url: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_archive_rpc: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    block_time_seconds: Mapped[Numeric] = mapped_column(Numeric(8, 3), nullable=False)


class ProtocolDeployment(TimestampMixin, Base):
    __tablename__ = "protocol_deployments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("networks.chain_id"), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)  # "aave_v3"
    market_name: Mapped[str] = mapped_column(String(64), nullable=False)
    pool_address: Mapped[str] = mapped_column(ADDR, nullable=False)
    pool_impl_address: Mapped[str | None] = mapped_column(ADDR)
    addresses_provider: Mapped[str] = mapped_column(ADDR, nullable=False)
    oracle_address: Mapped[str] = mapped_column(ADDR, nullable=False)
    data_provider_address: Mapped[str] = mapped_column(ADDR, nullable=False)
    ui_pool_data_provider: Mapped[str | None] = mapped_column(ADDR)
    address_book_version: Mapped[str] = mapped_column(String(32), nullable=False)
    bytecode_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flashloan_premium_total_bps: Mapped[int | None] = mapped_column(Integer)
    flashloan_premium_to_protocol_bps: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("chain_id", "pool_address", name="uq_deployment_chain_pool"),)


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("networks.chain_id"), nullable=False)
    address: Mapped[str] = mapped_column(ADDR, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    a_token: Mapped[str | None] = mapped_column(ADDR)
    variable_debt_token: Mapped[str | None] = mapped_column(ADDR)
    # reserve configuration (bps) as last observed; authoritative source is the chain
    ltv_bps: Mapped[int | None] = mapped_column(Integer)
    liquidation_threshold_bps: Mapped[int | None] = mapped_column(Integer)
    liquidation_bonus_bps: Mapped[int | None] = mapped_column(Integer)
    liquidation_protocol_fee_bps: Mapped[int | None] = mapped_column(Integer)
    reserve_factor_bps: Mapped[int | None] = mapped_column(Integer)
    emode_category: Mapped[int | None] = mapped_column(Integer)
    usage_as_collateral_enabled: Mapped[bool | None] = mapped_column(Boolean)
    borrowing_enabled: Mapped[bool | None] = mapped_column(Boolean)
    is_active: Mapped[bool | None] = mapped_column(Boolean)
    is_frozen: Mapped[bool | None] = mapped_column(Boolean)
    is_paused: Mapped[bool | None] = mapped_column(Boolean)
    config_observed_block: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (UniqueConstraint("chain_id", "address", name="uq_asset_chain_address"),)


class Borrower(TimestampMixin, Base):
    __tablename__ = "borrowers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("networks.chain_id"), nullable=False)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("protocol_deployments.id"), nullable=False)
    address: Mapped[str] = mapped_column(ADDR, nullable=False)
    first_seen_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_event_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # latest reconciled account data (from getUserAccountData); raw units
    health_factor_wad: Mapped[int | None] = mapped_column(RAW)
    total_collateral_base: Mapped[int | None] = mapped_column(RAW)
    total_debt_base: Mapped[int | None] = mapped_column(RAW)
    available_borrows_base: Mapped[int | None] = mapped_column(RAW)
    current_liquidation_threshold_bps: Mapped[int | None] = mapped_column(Integer)
    ltv_bps: Mapped[int | None] = mapped_column(Integer)
    emode_category: Mapped[int | None] = mapped_column(Integer)
    last_reconciled_block: Mapped[int | None] = mapped_column(BigInteger)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority_score: Mapped[Numeric | None] = mapped_column(Numeric(20, 8))
    __table_args__ = (
        UniqueConstraint("chain_id", "deployment_id", "address", name="uq_borrower"),
        Index("ix_borrowers_hf", "chain_id", "health_factor_wad"),
    )


class BorrowerReserveState(TimestampMixin, Base):
    __tablename__ = "borrower_reserve_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    borrower_id: Mapped[int] = mapped_column(ForeignKey("borrowers.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    a_token_balance: Mapped[int] = mapped_column(RAW, default=0, nullable=False)
    variable_debt: Mapped[int] = mapped_column(RAW, default=0, nullable=False)
    stable_debt: Mapped[int] = mapped_column(RAW, default=0, nullable=False)
    usage_as_collateral: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    observed_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint("borrower_id", "asset_id", name="uq_borrower_reserve"),)


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    borrower_id: Mapped[int] = mapped_column(ForeignKey("borrowers.id", ondelete="CASCADE"), nullable=False)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str | None] = mapped_column(HASH)
    health_factor_wad: Mapped[int] = mapped_column(RAW, nullable=False)
    total_collateral_base: Mapped[int] = mapped_column(RAW, nullable=False)
    total_debt_base: Mapped[int] = mapped_column(RAW, nullable=False)
    reserves: Mapped[dict] = mapped_column(JSON, nullable=False)  # [{asset, a_token_balance, variable_debt, collateral}]
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_snapshots_borrower_block", "borrower_id", "block_number"),)


class OraclePriceObservation(Base):
    __tablename__ = "oracle_price_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    asset_address: Mapped[str] = mapped_column(ADDR, nullable=False)
    price_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    price_decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str | None] = mapped_column(HASH)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_prices_chain_asset_block", "chain_id", "asset_address", "block_number"),)


class LiquidationCandidate(TimestampMixin, Base):
    __tablename__ = "liquidation_candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    borrower_id: Mapped[int] = mapped_column(ForeignKey("borrowers.id", ondelete="CASCADE"), nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str | None] = mapped_column(HASH)
    status: Mapped[CandidateStatus] = mapped_column(Enum(CandidateStatus), nullable=False)
    outcome_class: Mapped[OutcomeClass] = mapped_column(Enum(OutcomeClass), nullable=False)
    health_factor_wad: Mapped[int] = mapped_column(RAW, nullable=False)
    debt_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    collateral_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    debt_to_cover_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    max_debt_to_cover_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    close_factor_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    collateral_seized_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    protocol_fee_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    liquidation_bonus_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    flash_premium_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    swap_quote_id: Mapped[int | None] = mapped_column(ForeignKey("swap_quotes.id"))
    swap_out_raw: Mapped[int | None] = mapped_column(RAW)
    gas_units: Mapped[int | None] = mapped_column(BigInteger)
    gas_price_wei: Mapped[int | None] = mapped_column(RAW)
    l1_data_fee_wei: Mapped[int | None] = mapped_column(RAW)     # estimated at candidate time
    fee_source: Mapped[str | None] = mapped_column(String(16))   # estimated | unknown | not_applicable
    gas_cost_usd: Mapped[Numeric | None] = mapped_column(USD)    # L2 execution + L1 data (estimated), in USD
    gross_bonus_usd: Mapped[Numeric | None] = mapped_column(USD)
    net_profit_debt_raw: Mapped[int | None] = mapped_column(RAW)
    net_profit_usd: Mapped[Numeric | None] = mapped_column(USD)
    return_on_notional: Mapped[Numeric | None] = mapped_column(Numeric(20, 10))
    breakeven_gas_usd: Mapped[Numeric | None] = mapped_column(USD)
    breakeven_slippage_bps: Mapped[int | None] = mapped_column(Integer)
    max_rational_bid_usd: Mapped[Numeric | None] = mapped_column(USD)
    incomplete_reasons: Mapped[list | None] = mapped_column(JSON)
    breakdown: Mapped[dict | None] = mapped_column(JSON)
    data_sources: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (Index("ix_candidates_chain_status", "chain_id", "status"),)


class HistoricalLiquidation(TimestampMixin, Base):
    __tablename__ = "historical_liquidations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("protocol_deployments.id"), nullable=False)
    tx_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    block_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tx_index: Mapped[int | None] = mapped_column(Integer)
    borrower: Mapped[str] = mapped_column(ADDR, nullable=False)
    liquidator: Mapped[str] = mapped_column(ADDR, nullable=False)
    debt_asset: Mapped[str] = mapped_column(ADDR, nullable=False)
    collateral_asset: Mapped[str] = mapped_column(ADDR, nullable=False)
    debt_repaid_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    collateral_seized_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    receive_a_token: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gas_used: Mapped[int | None] = mapped_column(BigInteger)
    effective_gas_price_wei: Mapped[int | None] = mapped_column(RAW)
    tx_status: Mapped[int | None] = mapped_column(Integer)
    l1_fee_wei: Mapped[int | None] = mapped_column(RAW)          # verified from the receipt (l1Fee / gasUsedForL1)
    total_fee_wei: Mapped[int | None] = mapped_column(RAW)       # L2 execution + L1 data, verified
    fee_source: Mapped[str | None] = mapped_column(String(16))   # verified | unknown | not_applicable
    pool_impl_address: Mapped[str | None] = mapped_column(ADDR)
    debt_repaid_usd: Mapped[Numeric | None] = mapped_column(USD)
    collateral_seized_usd: Mapped[Numeric | None] = mapped_column(USD)
    __table_args__ = (
        UniqueConstraint("chain_id", "tx_hash", "log_index", name="uq_hist_liq_event"),
        Index("ix_hist_liq_chain_block", "chain_id", "block_number"),
    )


class SwapQuote(TimestampMixin, Base):
    __tablename__ = "swap_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_in: Mapped[str] = mapped_column(ADDR, nullable=False)
    token_out: Mapped[str] = mapped_column(ADDR, nullable=False)
    amount_in_raw: Mapped[int] = mapped_column(RAW, nullable=False)
    amount_out_raw: Mapped[int | None] = mapped_column(RAW)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. uniswap_v3_quoter_v2
    route: Mapped[dict | None] = mapped_column(JSON)
    price_impact_bps: Mapped[int | None] = mapped_column(Integer)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | incomplete | error
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimulationRun(TimestampMixin, Base):
    __tablename__ = "simulation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # candidate | historical_replay | manual
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), nullable=False)
    outcome_class: Mapped[OutcomeClass] = mapped_column(Enum(OutcomeClass), default=OutcomeClass.simulated, nullable=False)
    fork_block_number: Mapped[int | None] = mapped_column(BigInteger)
    fork_block_hash: Mapped[str | None] = mapped_column(HASH)
    block_boundary_approximation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("liquidation_candidates.id"))
    historical_liquidation_id: Mapped[int | None] = mapped_column(ForeignKey("historical_liquidations.id"))
    pool_address: Mapped[str | None] = mapped_column(ADDR)
    pool_impl_address: Mapped[str | None] = mapped_column(ADDR)
    input_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    code_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rpc_capability: Mapped[dict | None] = mapped_column(JSON)
    strategy_key: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int | None] = mapped_column(Integer)     # fork attempts made for this run (retries included)
    batch: Mapped[str | None] = mapped_column(String(32))     # e.g. phase3-base; groups runs for reporting
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class SimulationResult(TimestampMixin, Base):
    __tablename__ = "simulation_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revert_reason: Mapped[str | None] = mapped_column(Text)
    debt_repaid_raw: Mapped[int | None] = mapped_column(RAW)
    collateral_seized_raw: Mapped[int | None] = mapped_column(RAW)
    swap_proceeds_raw: Mapped[int | None] = mapped_column(RAW)
    flash_premium_raw: Mapped[int | None] = mapped_column(RAW)
    gas_used: Mapped[int | None] = mapped_column(BigInteger)
    gas_price_wei: Mapped[int | None] = mapped_column(RAW)
    net_profit_debt_raw: Mapped[int | None] = mapped_column(RAW)
    net_profit_usd: Mapped[Numeric | None] = mapped_column(USD)
    trace_summary: Mapped[dict | None] = mapped_column(JSON)
    calldata_hex: Mapped[str | None] = mapped_column(Text)  # local simulation only; never broadcast
    fees: Mapped[dict | None] = mapped_column(JSON)          # FeeAccounting.as_dict(): l2/l1/total wei + source
    execution_path: Mapped[str | None] = mapped_column(String(32))  # flash_loan_receiver | direct_liquidation_call
    surplus_raw: Mapped[int | None] = mapped_column(RAW)      # receiver event surplus (swapOut − repaid − premium)
    gas_cost_usd: Mapped[Numeric | None] = mapped_column(USD) # L2 execution + L1 data, USD
    profit_complete: Mapped[bool | None] = mapped_column(Boolean)  # every material cost known
    eligible_at_fork: Mapped[bool | None] = mapped_column(Boolean)       # HF < 1 at the fork block (N-1 state)
    eligible_at_execution: Mapped[bool | None] = mapped_column(Boolean)  # HF < 1 at the execution block's timestamp, before block N's txs
    reconciliation: Mapped[dict | None] = mapped_column(JSON) # ProfitReconciliation.as_dict()
    warnings: Mapped[list | None] = mapped_column(JSON)
    assumptions: Mapped[list | None] = mapped_column(JSON)


class StrategyDefinition(TimestampMixin, Base):
    __tablename__ = "strategy_definitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # A | B | C
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StrategyRun(TimestampMixin, Base):
    __tablename__ = "strategy_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy_definitions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # backtest | montecarlo | shadow
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), nullable=False)
    outcome_class: Mapped[OutcomeClass] = mapped_column(Enum(OutcomeClass), nullable=False)
    chain_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    date_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    random_seed: Mapped[int | None] = mapped_column(BigInteger)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSON)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    assumptions: Mapped[list | None] = mapped_column(JSON)
    data_quality: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompetitorObservation(TimestampMixin, Base):
    __tablename__ = "competitor_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    liquidator: Mapped[str] = mapped_column(ADDR, nullable=False)
    historical_liquidation_id: Mapped[int | None] = mapped_column(ForeignKey("historical_liquidations.id"))
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("liquidation_candidates.id"))
    eligibility_block: Mapped[int | None] = mapped_column(BigInteger)  # earliest block we observed eligibility
    liquidation_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocks_to_liquidation: Mapped[int | None] = mapped_column(Integer)
    gas_used: Mapped[int | None] = mapped_column(BigInteger)
    priority_fee_wei: Mapped[int | None] = mapped_column(RAW)
    notes: Mapped[str | None] = mapped_column(Text)


class RpcHealthMetric(Base):
    __tablename__ = "rpc_health_metrics"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    endpoint_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # http | ws | archive
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_rpc_health_chain_time", "chain_id", "observed_at"),)


class WorkerJobRun(Base):
    __tablename__ = "worker_job_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_job_runs_name_time", "job_name", "started_at"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IndexerCheckpoint(TimestampMixin, Base):
    """Not in the spec's minimum list, but required for bounded, resumable backfill (§5.2)."""
    __tablename__ = "indexer_checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("protocol_deployments.id"), nullable=False)
    stream: Mapped[str] = mapped_column(String(32), nullable=False)  # pool_events
    last_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_block_hash: Mapped[str | None] = mapped_column(HASH)
    recent_block_hashes: Mapped[dict | None] = mapped_column(JSON)  # {block_number: hash} for reorg detection
    __table_args__ = (UniqueConstraint("chain_id", "deployment_id", "stream", name="uq_checkpoint"),)


class PoolEvent(Base):
    """Raw decoded Aave pool events, idempotent on (chain, tx, log_index). Retention-managed."""
    __tablename__ = "pool_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("protocol_deployments.id"), nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    tx_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_name: Mapped[str] = mapped_column(String(48), nullable=False)
    user: Mapped[str | None] = mapped_column(ADDR)
    reserve: Mapped[str | None] = mapped_column(ADDR)
    args: Mapped[dict] = mapped_column(JSON, nullable=False)
    orphaned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("chain_id", "tx_hash", "log_index", name="uq_pool_event"),
        Index("ix_pool_events_chain_block", "chain_id", "block_number"),
        Index("ix_pool_events_user", "chain_id", "user"),
    )
