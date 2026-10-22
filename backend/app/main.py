from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import redis as redis_sync
import redis.asyncio as redis_async
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Gauge
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes_competition, routes_core, routes_data, routes_research, routes_sim, routes_ws
from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.chains.validation import verify_deployment
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import RateLimiter

RPC_UP = Gauge("fll_rpc_up", "RPC endpoint reachable", ["network", "kind"])
BLOCK_HEIGHT = Gauge("fll_block_height", "Latest block seen", ["network"])


def _refresh_rpc_health(app: FastAPI) -> None:
    for slug, clients in app.state.rpc_clients.items():
        for kind, client in clients.items():
            h = client.health()
            app.state.rpc_health[(slug, kind)] = h
            RPC_UP.labels(slug, kind).set(1 if h.ok else 0)
            if h.block_number:
                BLOCK_HEIGHT.labels(slug).set(h.block_number)


async def _health_loop(app: FastAPI, interval: int):
    while True:
        try:
            await asyncio.to_thread(_refresh_rpc_health, app)
        except Exception as e:  # never let the loop die
            get_logger(__name__).warning("health_loop_error", error=str(e)[:200])
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, json_output=s.app_env != "development")
    log = get_logger("startup")
    app.state.networks = {k: v for k, v in build_networks(s).items() if v.enabled}
    app.state.rpc_clients = {
        slug: {
            "http": RpcClient(slug, n.rpc_http, s.rpc_timeout_seconds, s.rpc_max_retries),
            "archive": RpcClient(f"{slug}-archive", n.archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries),
        }
        for slug, n in app.state.networks.items()
    }
    app.state.rpc_health = {}
    app.state.redis = redis_sync.Redis.from_url(s.redis_url, socket_timeout=2)
    app.state.redis_async = redis_async.Redis.from_url(s.redis_url)
    app.state.rate_limiter = RateLimiter(s.api_rate_limit_per_minute, app.state.redis)
    app.state.simulation_health = lambda: {"anvil_url_configured": bool(s.anvil_rpc_url), "status": "not_implemented",
                                           "note": "fork simulation service lands in milestone 4"}
    await asyncio.to_thread(_refresh_rpc_health, app)
    app.state.deployment_verification = {
        slug: verify_deployment(n, app.state.rpc_clients[slug]["http"]) for slug, n in app.state.networks.items()
    }
    for slug, v in app.state.deployment_verification.items():
        log.info("network_status", network=slug, verified=v["verified"], error=v["error"])
    task = asyncio.create_task(_health_loop(app, max(10, s.monitor_interval_seconds)))
    log.info("api_started", mode=s.app_mode, live_trading=s.live_trading_enabled, networks=list(app.state.networks))
    try:
        yield
    finally:
        task.cancel()
        await app.state.redis_async.aclose()
        app.state.redis.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="FlashLiquidator Lab API", version=s.app_version, lifespan=lifespan,
                  description="Aave V3 liquidation research platform. Simulation only: no transaction submission exists.")
    app.include_router(routes_core.router)
    app.include_router(routes_data.router)
    app.include_router(routes_sim.router)
    app.include_router(routes_competition.router)
    app.include_router(routes_research.router)
    app.include_router(routes_ws.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": exc.detail if isinstance(exc.detail, str) else "error", "detail": exc.detail},
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def val_exc(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": "validation_error", "detail": exc.errors()}, status_code=422)

    return app


app = create_app()
