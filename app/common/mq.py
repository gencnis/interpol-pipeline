from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pika  # type: ignore[import-untyped]
import pika.channel  # type: ignore[import-untyped]
import pika.spec  # type: ignore[import-untyped]

from app.common.config import Settings
from app.common.logging import get_logger

log = get_logger(__name__)

_MessageCallback = Callable[
    [pika.channel.Channel, pika.spec.Basic.Deliver, pika.spec.BasicProperties, bytes],
    None,
]


class MQClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.channel.Channel | None = None

    def connect(self) -> None:
        params = pika.URLParameters(self._settings.RABBITMQ_URL)
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self._declare_topology()

    def _declare_topology(self) -> None:
        s = self._settings
        ch = self._channel
        assert ch is not None

        ch.exchange_declare(exchange=s.MQ_EXCHANGE, exchange_type="topic", durable=True)

        ch.queue_declare(queue=s.MQ_DLQ, durable=True)

        ch.queue_declare(
            queue=s.MQ_WORK_QUEUE,
            durable=True,
            arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": s.MQ_DLQ,
            },
        )

        ch.queue_bind(exchange=s.MQ_EXCHANGE, queue=s.MQ_WORK_QUEUE, routing_key="notice.*")

        log.info("mq.topology_declared", exchange=s.MQ_EXCHANGE, queue=s.MQ_WORK_QUEUE)

    def publish(self, routing_key: str, payload: dict[str, Any]) -> None:
        assert self._channel is not None
        body = json.dumps(payload, default=str).encode()
        self._channel.basic_publish(
            exchange=self._settings.MQ_EXCHANGE,
            routing_key=routing_key,
            body=body,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
            ),
        )

    def consume(self, queue: str, callback: _MessageCallback) -> None:
        assert self._channel is not None
        self._channel.basic_qos(prefetch_count=1)
        self._channel.basic_consume(queue=queue, on_message_callback=callback)
        self._channel.start_consuming()

    def stop_consuming(self) -> None:
        if self._connection and not self._connection.is_closed:
            ch = self._channel
            self._connection.add_callback_threadsafe(
                lambda: ch.stop_consuming() if ch else None
            )

    def close(self) -> None:
        try:
            if self._connection and not self._connection.is_closed:
                self._connection.close()
        except Exception:
            pass
