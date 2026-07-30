import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pika
import pika.exceptions

from .config import settings

EXCHANGE_NAME = "webshop.events"
INVOICE_QUEUE_NAME = "invoice-service.commands"

logger = logging.getLogger(__name__)

_INITIAL_RECONNECT_DELAY_SECONDS = 2
_MAX_RECONNECT_DELAY_SECONDS = 30


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


def _connect_with_retry(stop_event: threading.Event) -> pika.BlockingConnection | None:
    """Verbindet mit RabbitMQ, mit Backoff-Retry bei Start-/Verbindungsproblemen.

    Gibt None zurueck, wenn stop_event waehrend des Wartens gesetzt wurde
    (sauberer Shutdown statt Endlosschleife).
    """
    delay = _INITIAL_RECONNECT_DELAY_SECONDS
    while not stop_event.is_set():
        try:
            parameters = pika.URLParameters(settings.rabbitmq_url)
            return pika.BlockingConnection(parameters)
        except pika.exceptions.AMQPConnectionError:
            logger.warning(
                "RabbitMQ nicht erreichbar, naechster Verbindungsversuch in %ss",
                delay,
            )
            stop_event.wait(delay)
            delay = min(delay * 2, _MAX_RECONNECT_DELAY_SECONDS)
    return None


def consume_messages(
        routing_keys: list[str],
        handle_message: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
) -> None:
    # Aeussere Schleife: baut die Verbindung neu auf, sobald sie einmal
    # verloren geht (Start-Race, RabbitMQ-Neustart, Netzwerk-Hiccup, ...).
    # Ohne das stirbt der Consumer-Thread bei jedem Verbindungsproblem
    # endgueltig, und es werden nie wieder Nachrichten verarbeitet.
    while not stop_event.is_set():
        connection = _connect_with_retry(stop_event)
        if connection is None:
            return  # stop_event wurde gesetzt, bevor eine Verbindung stand

        channel = None
        try:
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=INVOICE_QUEUE_NAME, durable=True)
            for routing_key in routing_keys:
                channel.queue_bind(
                    queue=INVOICE_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=routing_key
                )

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
        except (
                pika.exceptions.AMQPConnectionError,
                pika.exceptions.StreamLostError,
                pika.exceptions.ChannelClosedByBroker,
                pika.exceptions.ConnectionClosedByBroker,
        ) as exc:
            if stop_event.is_set():
                return
            logger.warning("RabbitMQ-Verbindung verloren (%s), verbinde neu...", exc)
            continue
        finally:
            try:
                if channel is not None:
                    channel.cancel()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
