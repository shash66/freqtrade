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
