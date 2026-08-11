import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pika

from .config import settings

EXCHANGE_NAME = "webshop.events"
INVOICE_QUEUE_NAME = "invoice-service.commands"

logger = logging.getLogger(__name__)


def build_message(
    message_type: str,
    correlation_id: str,
    payload: dict[str, Any],
    previous_event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "messageId": str(uuid4()),
        "correlationId": correlation_id,
        "type": message_type,
        "sourceService": settings.service_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "previousEventId": previous_event_id,
    }


def publish_message(routing_key: str, message: dict[str, Any]) -> None:
    parameters = pika.URLParameters(settings.rabbitmq_url)
    connection = pika.BlockingConnection(parameters)
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
        channel.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key=routing_key,
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
            ),
        )
    finally:
        connection.close()


def consume_messages(
    routing_keys: list[str],
    handle_message: Callable[[dict[str, Any]], None],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        connection = None
        channel = None
        try:
            parameters = pika.URLParameters(settings.rabbitmq_url)
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=INVOICE_QUEUE_NAME, durable=True)
            for routing_key in routing_keys:
                channel.queue_bind(queue=INVOICE_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=routing_key)
            logger.info("RabbitMQ invoice consumer connected")

            for method_frame, properties, body in channel.consume(
                INVOICE_QUEUE_NAME,
                inactivity_timeout=1,
                auto_ack=False,
            ):
                if stop_event.is_set():
                    break
                if method_frame is None:
                    continue
                try:
                    handle_message(json.loads(body.decode("utf-8")))
                    channel.basic_ack(method_frame.delivery_tag)
                except Exception:
                    logger.exception("Failed to handle invoice message")
                    channel.basic_nack(method_frame.delivery_tag, requeue=False)
        except Exception:
            if not stop_event.is_set():
                logger.exception("RabbitMQ invoice consumer disconnected; retrying")
                time.sleep(2)
        finally:
            if channel and channel.is_open:
                try:
                    channel.cancel()
                except Exception:
                    logger.exception("Failed to cancel invoice consumer channel")
            if connection and connection.is_open:
                connection.close()
