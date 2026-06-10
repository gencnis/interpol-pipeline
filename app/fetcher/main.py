from __future__ import annotations

import signal
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger
from app.common.mq import MQClient
from app.fetcher.client import InterpolClient
from app.fetcher.publisher import FetchPublisher
from app.fetcher.sweep import SweepStrategy

_HEALTH_PORT = 8080
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


def _connect_with_retry(mq: MQClient, stop: threading.Event) -> bool:
    log = get_logger(__name__)
    while not stop.is_set():
        try:
            mq.connect()
            log.info("fetcher.mq_connected")
            return True
        except Exception as exc:
            log.warning("fetcher.mq_connect_failed", error=str(exc), retry_in=_MQ_RETRY_DELAY)
            stop.wait(_MQ_RETRY_DELAY)
    return False


def _run_loop(publisher: FetchPublisher, interval: int, stop: threading.Event) -> None:
    log = get_logger(__name__)
    while not stop.is_set():
        try:
            publisher.run_cycle()
        except Exception as exc:
            log.error("fetcher.cycle_error", error=str(exc))
        stop.wait(interval)


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    _start_health_server()
    log.info("fetcher.starting", health_port=_HEALTH_PORT)

    stop = threading.Event()

    def _handle(signum: int, frame: types.FrameType | None) -> None:
        log.info("fetcher.shutdown", signum=signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    mq = MQClient(settings)
    http_client = InterpolClient(settings)
    sweep = SweepStrategy(http_client, settings)

    if not _connect_with_retry(mq, stop):
        return

    publisher = FetchPublisher(sweep, mq, settings)

    threading.Thread(
        target=_run_loop,
        args=(publisher, settings.FETCH_INTERVAL_SECONDS, stop),
        daemon=True,
    ).start()

    stop.wait()
    http_client.close()
    mq.close()
    log.info("fetcher.stopped")


if __name__ == "__main__":
    main()
