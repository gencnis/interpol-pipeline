from __future__ import annotations

import json
import signal
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pika.channel
import pika.spec

from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger
from app.common.mq import MQClient

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


def _on_message(
    channel: pika.channel.Channel,
    method: pika.spec.Basic.Deliver,
    _properties: pika.spec.BasicProperties,
    body: bytes,
) -> None:
    log = get_logger(__name__)
    try:
        payload = json.loads(body)
        log.info(
            "worker.received",
            routing_key=method.routing_key,
            notice_id=payload.get("notice_id"),
            cycle_id=payload.get("cycle_id"),
        )
    except Exception as exc:
        log.error("worker.decode_error", error=str(exc))
    channel.basic_ack(delivery_tag=method.delivery_tag)


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

    stop = threading.Event()

    def _handle(signum: int, frame: types.FrameType | None) -> None:
        log.info("worker.shutdown", signum=signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    mq = MQClient(settings)
    if not _connect_with_retry(mq, stop):
        return

    consumer_thread = threading.Thread(
        target=mq.consume,
        args=(settings.MQ_WORK_QUEUE, _on_message),
        daemon=True,
    )
    consumer_thread.start()
    log.info("worker.consuming", queue=settings.MQ_WORK_QUEUE)

    stop.wait()
    mq.stop_consuming()
    consumer_thread.join(timeout=5)
    mq.close()
    log.info("worker.stopped")


if __name__ == "__main__":
    main()
