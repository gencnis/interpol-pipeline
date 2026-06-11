from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis as AioRedis

from app.common.config import get_settings

router = APIRouter()


@router.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    settings = get_settings()
    await ws.accept()
    redis: AioRedis[str] = AioRedis.from_url(  # type: ignore[type-arg]
        settings.REDIS_URL, decode_responses=True
    )
    pubsub = redis.pubsub()
    await pubsub.subscribe(settings.REDIS_EVENT_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = message["data"]
                await ws.send_text(data if isinstance(data, str) else data.decode())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await pubsub.unsubscribe(settings.REDIS_EVENT_CHANNEL)
        await redis.aclose()
