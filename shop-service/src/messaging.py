"""RabbitMQ-Anbindung des Shop-Service."""

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pika
import pika.exceptions

from .config import settings

# Gemeinsamer Topic-Exchange aller Services.
EXCHANGE_NAME = "webshop.events"
# Queue fuer Saga-Ereignisse des Shop-Service.
SHOP_QUEUE_NAME = "shop-service.saga"

logger = logging.getLogger(__name__)

_INITIAL_RECONNECT_DELAY_SECONDS = 2
_MAX_RECONNECT_DELAY_SECONDS = 30
# Begrenzte Wiederholungsversuche beim Publizieren.
_PUBLISH_MAX_ATTEMPTS = 3
_PUBLISH_RETRY_BACKOFF_SECONDS = 1.0


def build_message(
        message_type: str,
        correlation_id: str,
        payload: dict[str, Any],
        previous_event_id: str | None = None,
) -> dict[str, Any]:
    """Erzeugt den gemeinsamen Nachrichtenumschlag."""
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
    """Veroeffentlicht eine Nachricht mit begrenzten Wiederholungsversuchen."""
    last_exc: Exception | None = None
    for attempt in range(1, _PUBLISH_MAX_ATTEMPTS + 1):
        try:
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
                return
            finally:
                connection.close()
        except (pika.exceptions.AMQPError, OSError) as exc:
            # OSError deckt auch DNS-Fehler ab.
            last_exc = exc
            if attempt < _PUBLISH_MAX_ATTEMPTS:
                logger.warning(
                    "RabbitMQ-Publish fehlgeschlagen (Versuch %s/%s), naechster Versuch in %ss: %s",
                    attempt,
                    _PUBLISH_MAX_ATTEMPTS,
                    _PUBLISH_RETRY_BACKOFF_SECONDS * attempt,
                    exc,
                )
                time.sleep(_PUBLISH_RETRY_BACKOFF_SECONDS * attempt)
    raise last_exc


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


def consume_messages(
        routing_keys: list[str],
        handle_message: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
) -> None:
    """Konsumiert Nachrichten blockierend und verbindet bei Ausfall neu."""
    while not stop_event.is_set():
        connection = _connect_with_retry(stop_event)
        if connection is None:
            return  # stop_event wurde gesetzt, bevor eine Verbindung stand

        channel = None
        try:
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=SHOP_QUEUE_NAME, durable=True)
            for routing_key in routing_keys:
                channel.queue_bind(
                    queue=SHOP_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=routing_key
                )

            for method_frame, properties, body in channel.consume(
                    SHOP_QUEUE_NAME,
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
                    logger.exception("Failed to handle shop message")
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
