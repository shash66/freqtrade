"""Protocol adapter interface (Phase 4, priority 6).

A lending market is described by a `MarketSpec` (what it is, where it lives, how its deployment is verified)
and served by a `ProtocolAdapter` (how to read positions, decide eligibility, value a liquidation, and find
historical liquidations). The research tools talk to adapters, never to a protocol directly, so a new market
is a new adapter plus a spec; the correctness rules of this project still apply to every adapter:

- every figure comes from a contract read or an event, never from a placeholder;
- math follows the protocol's own source (cite the file in the adapter docstring) and is unit-tested;
- anything the adapter cannot source is reported `incomplete`/`unknown`, not estimated;
- deployment verification (chain id, bytecode present, a round-trip read that only the real contract answers)
  runs before any data from the market is trusted; markets whose RPC host is unreachable report `blocked`.

Only read paths exist. No adapter builds, signs or sends a transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class MarketSpec:
    protocol: str            # aave_v3 | compound_v3
    slug: str                # e.g. aave_v3_base, compound_v3_base_usdc
    network: str             # network slug (chains.config)
    chain_id: int
    name: str
    primary_address: str     # Pool (Aave) / Comet (Compound)
    oracle_address: str | None
    source: str              # where the addresses come from (address book version, docs page)
    extra: dict = field(default_factory=dict)


@dataclass
class Verification:
    market: str
    status: str              # verified | failed | blocked
    chain_id_ok: bool | None = None
    bytecode_ok: bool | None = None
    round_trip_ok: bool | None = None
    head_block: int | None = None
    detail: dict = field(default_factory=dict)
    reason: str | None = None


@dataclass
class PositionView:
    account: str
    block: int | str
    liquidatable: bool | None
    health_metric: str | None            # Aave: health factor; Compound: borrow/limit ratio (both labeled)
    debt_usd: Decimal | None
    collateral_usd: Decimal | None
    detail: dict = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)


@dataclass
class LiquidationValue:
    """Protocol-defined value of a liquidation to the liquidator, before swap/gas: outcome class theoretical."""
    repay_usd: Decimal | None
    seize_usd: Decimal | None
    gross_bonus_usd: Decimal | None
    mechanism: str                       # how the liquidator is paid (e.g. "collateral at bonus", "absorb + buyCollateral at discount")
    assumptions: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)


@dataclass
class HistoricalEvent:
    tx_hash: str
    log_index: int
    block_number: int
    liquidator: str
    account: str
    debt_asset: str | None
    collateral_asset: str | None
    repaid_raw: int | None
    seized_raw: int | None
    kind: str                            # LiquidationCall | AbsorbCollateral | ...


class ProtocolAdapter(Protocol):
    spec: MarketSpec

    def verify(self) -> Verification: ...
    def position(self, account: str, block="latest") -> PositionView: ...
    def value_liquidation(self, pos: PositionView, debt_asset: str | None, collateral_asset: str | None) -> LiquidationValue: ...
    def historical_liquidations(self, from_block: int, to_block: int) -> list[HistoricalEvent]: ...
