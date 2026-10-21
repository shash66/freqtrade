from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    error: str
    detail: Any | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class RpcStatus(BaseModel):
    kind: str
    configured: bool
    ok: bool
    latency_ms: int | None = None
    block_number: int | None = None
    chain_id: int | None = None
    error: str | None = None


class NetworkOut(BaseModel):
    slug: str
    chain_id: int
    name: str
    native_symbol: str
    explorer_url: str
    enabled: bool
    has_archive_rpc: bool
    pool_address: str
    oracle_address: str
    address_book_version: str
    rpc: list[RpcStatus]
    deployment_verified: bool
    verification_error: str | None = None


class Health(BaseModel):
    status: str  # ok | degraded
    version: str
    app_mode: str
    live_trading_enabled: bool = False
    database: bool
    redis: bool
    networks: dict[str, bool]
    time: datetime


class StrategyPerf(BaseModel):
    key: str
    name: str
    runs: int
    last_run_at: datetime | None = None
    summary: dict | None = None


class Overview(BaseModel):
    """All simulated figures carry their outcome class and are never actual revenue."""
    networks: list[NetworkOut]
    active_borrowers: dict[str, int]
    near_liquidation_positions: dict[str, int]
    eligible_positions: dict[str, int]
    profitable_simulated_opportunities: int
    estimated_total_simulated_profit_usd: Decimal
    outcome_class_note: str = "simulated: results of local fork simulations, not on-chain earnings"
    strategies: list[StrategyPerf]
    simulation_service: dict
    indexer: dict[str, dict]
    generated_at: datetime


class BorrowerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    network: str
    address: str
    health_factor: str | None  # Decimal as string; "Infinity" when no debt
    total_collateral_usd: str | None
    total_debt_usd: str | None
    current_liquidation_threshold_bps: int | None
    emode_category: int | None
    last_reconciled_block: int | None
    last_reconciled_at: datetime | None
    priority_score: str | None


class OpportunityOut(BaseModel):
    id: int
    network: str
    borrower: str
    health_factor: str
    debt_asset: str
    collateral_asset: str
    repayment: str
    repayment_usd: str | None
    bonus_bps: int
    flash_loan_fee: str
    swap_impact_bps: int | None
    gas_usd: str | None
    net_profit_usd: str | None
    status: str
    outcome_class: str
    block_number: int
    incomplete_reasons: list[str] | None = None
    created_at: datetime


class SimulationRequest(BaseModel):
    network: str
    borrower: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    collateral_asset: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    debt_asset: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    debt_to_cover_raw: str = Field(pattern=r"^[0-9]+$")
    block_number: int | None = None
    slippage_bps: int = Field(default=50, ge=1, le=5000)
    min_profit_usd: Decimal = Decimal("0")
    gas_price_wei: str | None = Field(default=None, pattern=r"^[0-9]+$")
    flash_loan_provider: str = "aave_v3"


class BacktestRequest(BaseModel):
    networks: list[str]
    date_from: datetime
    date_to: datetime
    strategies: list[str] = ["A", "B", "C"]
    debt_assets: list[str] | None = None
    collateral_assets: list[str] | None = None
    random_seed: int = 42


class JobAccepted(BaseModel):
    job_id: int
    status: str
    status_url: str
