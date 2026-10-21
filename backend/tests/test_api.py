"""API tests run against the real app with lifespan; RPCs are unconfigured so networks report unreachable."""
import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password


@pytest.fixture(scope="module")
def client():
    os.environ["ADMIN_PASSWORD_HASH"] = hash_password("testpass")
    os.environ["BASE_RPC_HTTP"] = ""
    os.environ["ARBITRUM_RPC_HTTP"] = ""
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def test_health_reports_degraded_without_rpc(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True and body["redis"] is True
    assert body["live_trading_enabled"] is False
    assert body["status"] == "degraded" and body["networks"] == {"base": False, "arbitrum": False}


def test_networks_expose_verified_addresses_but_not_rpc_urls(client):
    nets = client.get("/api/networks").json()
    slugs = {n["slug"]: n for n in nets}
    assert slugs["base"]["chain_id"] == 8453 and slugs["arbitrum"]["chain_id"] == 42161
    assert slugs["base"]["pool_address"].lower() == "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
    assert slugs["base"]["deployment_verified"] is False and "no RPC" in slugs["base"]["verification_error"]
    assert "http" not in str(slugs["base"]["rpc"][0].get("error", "")).lower() or "configured" in slugs["base"]["rpc"][0]["error"]


def test_overview_counts_are_zero_on_empty_db(client):
    o = client.get("/api/overview").json()
    assert o["profitable_simulated_opportunities"] == 0
    assert "simulated" in o["outcome_class_note"]


def test_metrics_require_admin(client):
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", auth=("admin", "wrong")).status_code == 401
    r = client.get("/api/metrics", auth=("admin", "testpass"))
    assert r.status_code == 200 and b"fll_rpc_up" in r.content


def test_public_settings_never_contain_urls(client):
    s = client.get("/api/settings/public").json()
    assert s["read_only"] is True and s["live_trading_enabled"] is False
    assert not any("://" in str(v) for v in s.values())


def test_websocket_hello(client):
    with client.websocket_connect("/ws/updates") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello" and msg["live_trading"] is False


def test_data_routes_empty_and_validation(client):
    assert client.get("/api/positions?limit=5").json()["total"] >= 0
    assert client.get("/api/liquidations/history?network=nope").status_code == 404
    assert client.get("/api/opportunities?status=bogus").status_code == 422
    assert client.get("/api/opportunities/999999999").status_code == 404
    assert client.get("/api/positions/base/0x0000000000000000000000000000000000000001").status_code == 404


def test_strategies_and_compare(client):
    ks = {s["key"] for s in client.get("/api/strategies").json()}
    assert ks == {"A", "B", "C"}
    r = client.post("/api/strategies/compare", json={"strategies": ["A", "C"], "seed": 1, "trials": 500})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome_class"] == "modeled_capturable" and set(body["results"]) == {"A", "C"}
    assert body["results"]["C"]["win_rate"] >= body["results"]["A"]["win_rate"]
    assert client.post("/api/strategies/compare", json={"strategies": ["Z"]}).status_code == 422


def test_competition_summary_shape(client):
    body = client.get("/api/competition/summary").json()
    assert body["competitors"]["outcome_class"] == "onchain_actual"
    assert set(body["competitors"]["networks"]) == {"base", "arbitrum"}
    assert "simulated profit is not earned revenue" in body["shadow"]["networks"]["base"]["note"]


def test_simulation_and_backtest_jobs_require_admin_and_queue(client):
    sim = {"network": "base", "borrower": "0x" + "1" * 40, "collateral_asset": "0x" + "2" * 40, "debt_asset": "0x" + "3" * 40,
           "debt_to_cover_raw": "1000000"}
    assert client.post("/api/simulations", json=sim).status_code == 401
    r = client.post("/api/simulations", json=sim, auth=("admin", "testpass"))
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job = client.get(r.json()["status_url"]).json()
    assert job["kind"] == "manual" and job["status"] == "queued" and job["result"] is None
    bad = dict(sim, network="nope")
    assert client.post("/api/simulations", json=bad, auth=("admin", "testpass")).status_code == 404
    bt = {"networks": ["base"], "date_from": "2026-09-01T00:00:00Z", "date_to": "2026-10-01T00:00:00Z"}
    r = client.post("/api/backtests", json=bt, auth=("admin", "testpass"))
    assert r.status_code == 202
    b = client.get(r.json()["status_url"]).json()
    assert b["outcome_class"] == "simulated" and b["replays"] == []
    csv = client.get(r.json()["status_url"] + "?format=csv")
    assert csv.status_code == 200 and csv.text.startswith("sim_id,")
    assert client.post("/api/backtests", json=dict(bt, date_to=bt["date_from"]), auth=("admin", "testpass")).status_code == 422


def test_401_omits_browser_prompt_for_dashboard_fetches(client):
    r = client.get("/api/metrics")
    assert r.status_code == 401 and r.headers.get("www-authenticate") == "Basic"
    r = client.get("/api/metrics", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 401 and "www-authenticate" not in r.headers
    assert r.json()["error"] == "administrator credentials required"
