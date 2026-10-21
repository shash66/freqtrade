import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import redact
from app.core.security import hash_password, verify_password


def test_live_trading_cannot_be_enabled(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_defaults_are_simulation_only():
    s = Settings(_env_file=None)
    assert s.app_mode == "simulation" and s.live_trading_enabled is False


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)


def test_redaction_hides_rpc_keys_and_secrets():
    assert "<redacted>" in redact("https://base-mainnet.g.alchemy.com/v2/abcdefghijklmnopqrstuvwxyz")
    assert "hunter2" not in redact("password=hunter2 api_key: sk_live_1234567890123456")


def test_blank_env_values_are_unset(monkeypatch):
    monkeypatch.setenv("INDEXER_START_BLOCK_BASE", "")
    monkeypatch.setenv("BASE_RPC_HTTP", "")
    s = Settings(_env_file=None)
    assert s.indexer_start_block_base is None and s.base_rpc_http in ("", None)
    monkeypatch.setenv("INDEXER_START_BLOCK_BASE", "12345")
    assert Settings(_env_file=None).indexer_start_block_base == 12345
