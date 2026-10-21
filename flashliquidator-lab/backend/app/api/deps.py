from __future__ import annotations

from fastapi import Request


def get_networks(request: Request):
    return request.app.state.networks


def get_rpc_clients(request: Request):
    return request.app.state.rpc_clients


def get_redis(request: Request):
    return request.app.state.redis
