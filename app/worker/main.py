from __future__ import annotations

import functools
import json
import signal
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pika.channel  # type: ignore[import-untyped]
import pika.spec  # type: ignore[import-untyped]

from app.common.config import get_settings
from app.common.db import get_engine, make_session_factory, run_migrations, session_scope
from app.common.logging import configure_logging, get_logger
from app.common.mq import MQClient
from app.common.redis_client import RedisPublisher
from app.common.storage import StorageClient
from app.worker.photo_service import PhotoService
from app.worker.processor import NoticeProcessor

_HEALTH_PORT = 8081
_MQ_RETRY_DELAY = 5


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _start_health_server() -> None:
    server = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def _make_on_message(
    processor: NoticeProcessor,
) -> Any:
    log = get_logger(__name__)

    def _on_message(
        channel: pika.channel.Channel,
        method: pika.spec.Basic.Deliver,
        _properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        try:
            payload = json.loads(body)
            rk = method.routing_key
            if rk == "notice.upsert":
                processor.handle_upsert(payload)
            elif rk == "cycle.complete":
                processor.handle_cycle_complete(payload)
            else:
                log.warning("worker.unknown_routing_key", routing_key=rk)
        except Exception as exc:
            log.error("worker.message_error", error=str(exc), routing_key=method.routing_key)
        channel.basic_ack(delivery_tag=method.delivery_tag)

    return _on_message


def _connect_with_retry(mq: MQClient, stop: threading.Event) -> bool:
    log = get_logger(__name__)
    while not stop.is_set():
        try:
            mq.connect()
            log.info("worker.mq_connected")
            return True
        except Exception as exc:
            log.warning("worker.mq_connect_failed", error=str(exc), retry_in=_MQ_RETRY_DELAY)
            stop.wait(_MQ_RETRY_DELAY)
    return False


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    _start_health_server()
    log.info("worker.starting", health_port=_HEALTH_PORT)

    run_migrations(settings)

    engine = get_engine(settings)
    session_factory = make_session_factory(engine)
    get_session = functools.partial(session_scope, session_factory)

    storage = StorageClient(settings)
    storage.ensure_bucket()

    photo_service = PhotoService(storage, settings)
    redis_publisher = RedisPublisher(settings)
    processor = NoticeProcessor(
        get_session, photo_service, settings, redis_publisher=redis_publisher
    )

    stop = threading.Event()

    def _handle(signum: int, frame: types.FrameType | None) -> None:
        log.info("worker.shutdown", signum=signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    mq = MQClient(settings)
    if not _connect_with_retry(mq, stop):
        return

    on_message = _make_on_message(processor)
    consumer_thread = threading.Thread(
        target=mq.consume,
        args=(settings.MQ_WORK_QUEUE, on_message),
        daemon=True,
    )
    consumer_thread.start()
    log.info("worker.consuming", queue=settings.MQ_WORK_QUEUE)

    stop.wait()
    mq.stop_consuming()
    consumer_thread.join(timeout=5)
    mq.close()
    redis_publisher.close()
    engine.dispose()
    log.info("worker.stopped")


if __name__ == "__main__":
    main()
