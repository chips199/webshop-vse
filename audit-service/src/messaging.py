import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import pika

from .config import settings

EXCHANGE_NAME = "webshop.events"
AUDIT_QUEUE_NAME = "audit-service.snapshots"

logger = logging.getLogger(__name__)


def consume_audit_events(
    handle_message: Callable[[dict[str, Any]], None],
    stop_event: threading.Event,
) -> None:
    parameters = pika.URLParameters(settings.rabbitmq_url)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
    channel.queue_declare(queue=AUDIT_QUEUE_NAME, durable=True)
    channel.queue_bind(queue=AUDIT_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key="#")

    try:
        for method_frame, properties, body in channel.consume(
            AUDIT_QUEUE_NAME,
            inactivity_timeout=1,
            auto_ack=False,
        ):
            if stop_event.is_set():
                break
            if method_frame is None:
                continue

            try:
                message = json.loads(body.decode("utf-8"))
                handle_message(message)
                channel.basic_ack(method_frame.delivery_tag)
            except Exception:
                logger.exception("Failed to handle audit message")
                channel.basic_nack(method_frame.delivery_tag, requeue=False)
    finally:
        channel.cancel()
        connection.close()
