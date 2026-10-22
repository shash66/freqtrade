"""Research artefacts for the dashboard (Phase 4, priority 9): serves the JSON files the research tools write to the
docs directory (markets, observability, latency, strategies, bid model) as read-only data. The dashboard compares
networks, protocols, observable opportunity counts and execution latency from these files; nothing here is
computed on request, so what the page shows is exactly what the committed artefacts say."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import rate_limit

router = APIRouter(prefix="/api/research", tags=["research"], dependencies=[Depends(rate_limit)])
ARTEFACTS = {
    "markets": "phase4-markets.json",
    "observability": "phase4-observability-{net}.json",
    "latency": "phase4-latency-{net}.json",
    "strategies": "phase4-strategies-{net}.json",
    "bidmodel": "phase3-bidmodel-{net}.json",
    "replays": "phase3-replays-{net}.json",
    "benchmark": "phase3-benchmark-{net}.json",
    "timegate": "phase3-timegate-{net}.json",
}


def _docs_dir() -> Path:
    return Path(os.environ.get("RESEARCH_DOCS_DIR", str(Path(__file__).resolve().parents[3] / "docs")))


def _load(name: str) -> dict | None:
    p = _docs_dir() / name
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


@router.get("/summary")
def research_summary() -> dict:
    """Cross-network / cross-protocol comparison built from the artefacts: one row per network with observable
    opportunity counts, observable-before-winner rate, latency stages and strategy verdicts."""
    markets = _load(ARTEFACTS["markets"]) or {"markets": [], "summary": {}}
    rows = []
    for net in ("base", "arbitrum"):
        obs = _load(ARTEFACTS["observability"].format(net=net))
        lat = _load(ARTEFACTS["latency"].format(net=net))
        strat = _load(ARTEFACTS["strategies"].format(net=net))
        rep = _load(ARTEFACTS["replays"].format(net=net))
        rd = next((i["detail"] for i in (rep or {}).get("items", []) if i.get("key") == "replay"), {})
        rows.append({"network": net, "observability": (obs or {}).get("summary"), "latency_stages": (lat or {}).get("stages"),
                     "strategies": (strat or {}).get("verdict"), "replays": {k: rd.get(k) for k in ("liquidation_events", "unique_historical_txs", "simulation_attempts", "profitable", "net_profit_usd_total_complete")}})
    return {"generated_from": "committed research artefacts (read-only)", "markets": markets.get("summary"), "market_rows": [
        {k: m.get(k) for k in ("market", "protocol", "network", "chain_id", "name", "status", "recent_liquidation_events_last_blocks")} for m in markets.get("markets", [])],
        "networks": rows}


@router.get("/artefact/{kind}")
def research_artefact(kind: str, net: str = "base") -> dict:
    tpl = ARTEFACTS.get(kind)
    if tpl is None:
        raise HTTPException(404, "unknown artefact")
    doc = _load(tpl.format(net=net))
    if doc is None:
        raise HTTPException(404, "artefact not produced yet")
    return doc


# ---------------------------------------------------------------------------- Phase 5: strategy plugins
@router.get("/strategies")
def research_strategies() -> dict:
    """Unified strategy ranking (Phase 5 §6): one row per strategy × network built from docs/phase5-*.json by
    `app.plugins.ranking`. Simulated, theoretical, modeled and passive-yield figures are labeled per cell and
    never combined; nothing here is realized revenue (the lab has no execution path)."""
    from app.plugins.ranking import rank
    return rank(_docs_dir())


@router.get("/strategies/{strategy}/{net}")
def research_strategy_detail(strategy: str, net: str, mode: str = "discover") -> dict:
    """The raw plugin artefact for one strategy, network and mode (discover | simulate | replay | shadow)."""
    if mode not in ("discover", "simulate", "replay", "shadow") or not strategy.replace("_", "").isalnum() or not net.isalpha():
        raise HTTPException(404, "unknown artefact")
    doc = _load(f"phase5-{strategy}-{net}-{mode}.json")
    if doc is None:
        raise HTTPException(404, "artefact not produced yet")
    return doc
