"""RabbitMQ-Consumer des Audit-Service."""

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import pika
import pika.exceptions

from .config import settings

# Gemeinsamer Topic-Exchange aller Services.
EXCHANGE_NAME = "webshop.events"
# Queue fuer alle Ereignisse und Commands.
AUDIT_QUEUE_NAME = "audit-service.snapshots"

logger = logging.getLogger(__name__)

_INITIAL_RECONNECT_DELAY_SECONDS = 2
_MAX_RECONNECT_DELAY_SECONDS = 30


def _connect_with_retry(stop_event: threading.Event) -> pika.BlockingConnection | None:
    """Verbindet mit RabbitMQ und wiederholt Fehler mit Backoff."""
    delay = _INITIAL_RECONNECT_DELAY_SECONDS
    while not stop_event.is_set():
        try:
            parameters = pika.URLParameters(settings.rabbitmq_url)
            return pika.BlockingConnection(parameters)
        except (pika.exceptions.AMQPConnectionError, OSError) as exc:
            # pika kapselt DNS-Fehler nicht immer als AMQPConnectionError.
            logger.warning(
                "RabbitMQ nicht erreichbar (%s), naechster Verbindungsversuch in %ss",
                exc,
                delay,
            )
            stop_event.wait(delay)
            delay = min(delay * 2, _MAX_RECONNECT_DELAY_SECONDS)
    return None


def consume_audit_events(
        handle_message: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
) -> None:
    """Konsumiert alle Nachrichten blockierend und verbindet bei Ausfall neu."""
    while not stop_event.is_set():
        connection = _connect_with_retry(stop_event)
        if connection is None:
            return  # stop_event wurde gesetzt, bevor eine Verbindung stand

        channel = None
        try:
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=AUDIT_QUEUE_NAME, durable=True)
            # "#" bindet alle Routing-Keys des Topic-Exchange.
            channel.queue_bind(queue=AUDIT_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key="#")

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
        except (pika.exceptions.AMQPError, OSError) as exc:
            # Verbindungs- und Kanalfehler fuehren zum Neuaufbau der Verbindung.
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
