from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.common.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

_clients: set[WebSocket] = set()


async def broadcast_redis_events(redis_url: str, channel: str) -> None:
    """Long-running background task. Subscribes to Redis channel, broadcasts to WS clients."""
    r = aioredis.from_url(redis_url)
    try:
        async with r.pubsub() as ps:
            await ps.subscribe(channel)
            async for msg in ps.listen():
                if msg["type"] != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                dead: set[WebSocket] = set()
                for ws in list(_clients):
                    try:
                        await ws.send_text(data)
                    except Exception:
                        dead.add(ws)
                _clients.difference_update(dead)
    finally:
        await r.aclose()


@router.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _clients.discard(ws)


__all__ = ["router", "broadcast_redis_events"]
