from __future__ import annotations

import asyncio
import contextlib

from fastapi import WebSocket, WebSocketDisconnect

from app.common.logging import get_logger

log = get_logger(__name__)


async def ws_alerts(websocket: WebSocket) -> None:
    """Subscribe to Redis notice-events and stream them to the browser client.

    Uses a separate asyncio task for the Redis → WebSocket forwarding loop so
    that the main coroutine can block on `receive_text()` and detect client
    disconnection without interfering with the pump.
    """
    settings = websocket.app.state.settings
    redis_client = websocket.app.state.redis

    await websocket.accept()
    log.info("ws.client_connected")

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(settings.REDIS_EVENT_CHANNEL)

    async def _forward() -> None:
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    text = data.decode() if isinstance(data, bytes) else str(data)
                    await websocket.send_text(text)
        except Exception:
            pass

    fwd = asyncio.create_task(_forward())
    try:
        # Block until the client sends something (they won't) or closes the connection.
        await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        fwd.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await fwd
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(settings.REDIS_EVENT_CHANNEL)
            await pubsub.aclose()
        log.info("ws.client_disconnected")
