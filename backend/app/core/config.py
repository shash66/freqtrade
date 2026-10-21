"""Application settings. Everything comes from the environment; secrets never leave the server."""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_mode: Literal["simulation"] = "simulation"
    # Guard rail: the MVP has no execution path at all. This flag exists only so that a value
    # other than "false" aborts startup loudly instead of being silently ignored.
    live_trading_enabled: bool = False
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://fll:fll_dev_password@localhost:5432/flashliquidator"
    redis_url: str = "redis://localhost:6379/0"

    base_rpc_http: str = ""
    base_rpc_ws: str = ""
    base_archive_rpc: str = ""
    arbitrum_rpc_http: str = ""
    arbitrum_rpc_ws: str = ""
    arbitrum_archive_rpc: str = ""
    ethereum_rpc_http: str = ""
    ethereum_rpc_ws: str = ""
    ethereum_archive_rpc: str = ""
    ethereum_enabled: bool = False

    anvil_rpc_url: str = "http://anvil:8545"

    admin_username: str = "admin"
    # bcrypt hash of the admin password. Generate with: make admin-hash PASSWORD=...
    admin_password_hash: str = ""

    min_net_profit_usd: Decimal = Decimal(5)
    max_flash_loan_usd: Decimal = Decimal(100000)
    min_flash_loan_usd: Decimal = Decimal(1000)
    position_health_factor_alert: Decimal = Decimal("1.05")
    indexer_start_block_base: int | None = None
    indexer_start_block_arbitrum: int | None = None
    indexer_start_block_ethereum: int | None = None

    rpc_timeout_seconds: float = 10.0
    rpc_max_retries: int = 3
    monitor_interval_seconds: int = 30
    indexer_poll_interval_seconds: int = 12
    indexer_max_block_range: int = 2000
    reorg_safety_blocks: int = 12
    simulation_concurrency: int = 2
    api_rate_limit_per_minute: int = 120
    raw_data_retention_days: int = 30

    @field_validator("indexer_start_block_base", "indexer_start_block_arbitrum", "indexer_start_block_ethereum", mode="before")
    @classmethod
    def _blank_int_is_unset(cls, v):
        """`.env` files commonly leave optional values blank (KEY=); treat blank as unset instead of failing."""
        return None if isinstance(v, str) and v.strip() == "" else v

    @field_validator("live_trading_enabled")
    @classmethod
    def _no_live_trading(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "LIVE_TRADING_ENABLED=true is not supported by this build. The MVP has no transaction "
                "submission path; refusing to start."
            )
        return v

    def rpc_for(self, slug: str) -> tuple[str, str, str]:
        """(http, ws, archive) endpoints for a network slug."""
        return {
            "base": (self.base_rpc_http, self.base_rpc_ws, self.base_archive_rpc),
            "arbitrum": (self.arbitrum_rpc_http, self.arbitrum_rpc_ws, self.arbitrum_archive_rpc),
            "ethereum": (self.ethereum_rpc_http, self.ethereum_rpc_ws, self.ethereum_archive_rpc),
        }[slug]


@lru_cache
def get_settings() -> Settings:
    return Settings()
