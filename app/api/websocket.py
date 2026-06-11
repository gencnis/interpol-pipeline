from __future__ import annotations

import redis.asyncio  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, WebSocket

from app.api.dependencies import get_settings
from app.common.config import Settings

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/alerts")
async def ws_alerts(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
) -> None:
    await websocket.accept()
    r = redis.asyncio.from_url(settings.REDIS_URL)
    pubsub = r.pubsub()
    await pubsub.subscribe(settings.REDIS_EVENT_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                await websocket.send_text(data)
    except Exception:
        pass
    finally:
        await pubsub.unsubscribe()
        await r.aclose()
