from __future__ import annotations

import signal
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from app.common.config import get_settings
from app.common.logging import configure_logging, get_logger

_HEALTH_PORT = 8081


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    _start_health_server()
    log.info("worker.starting", health_port=_HEALTH_PORT)

    stop = threading.Event()

    def _handle(signum: int, frame: types.FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    stop.wait()
    log.info("worker.stopped")


if __name__ == "__main__":
    main()
