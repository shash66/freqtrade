"""WebSocket fan-out of update events published on Redis channel `fll:updates`."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

CHANNEL = "fll:updates"


@router.websocket("/ws/updates")
async def updates(ws: WebSocket):
    await ws.accept()
    redis = ws.app.state.redis_async
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        await ws.send_text(json.dumps({"type": "hello", "mode": "simulation", "live_trading": False}))
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if msg is not None:
                await ws.send_text(msg["data"] if isinstance(msg["data"], str) else msg["data"].decode())
            else:
                await ws.send_text(json.dumps({"type": "ping"}))
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
