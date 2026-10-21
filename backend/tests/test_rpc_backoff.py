import pytest

from app.chains.rpc import RpcClient, RpcUnavailable


def test_rate_limit_backoff_then_success(monkeypatch):
    monkeypatch.setattr("app.chains.rpc.time.sleep", lambda s: None)
    c = RpcClient("x", "http://unused", rate_limit_retries=3, min_interval_ms=0)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("429 Client Error: Too Many Requests")
        return "ok"
    assert c.call(fn) == "ok" and calls["n"] == 3


def test_rate_limit_gives_up_after_budget(monkeypatch):
    monkeypatch.setattr("app.chains.rpc.time.sleep", lambda s: None)
    c = RpcClient("x", "http://unused", rate_limit_retries=2, min_interval_ms=0)
    with pytest.raises(RpcUnavailable):
        c.call(lambda: (_ for _ in ()).throw(ValueError("You reached Public endpoint limit")))


def test_transient_error_retries_bounded(monkeypatch):
    monkeypatch.setattr("app.chains.rpc.time.sleep", lambda s: None)
    c = RpcClient("x", "http://unused", max_retries=2, min_interval_ms=0)
    with pytest.raises(RpcUnavailable):
        c.call(lambda: (_ for _ in ()).throw(OSError("connection reset")))
