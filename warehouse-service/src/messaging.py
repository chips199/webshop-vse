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

EXCHANGE_NAME = "webshop.events"
WAREHOUSE_QUEUE_NAME = "warehouse-service.commands"

logger = logging.getLogger(__name__)

_INITIAL_RECONNECT_DELAY_SECONDS = 2
_MAX_RECONNECT_DELAY_SECONDS = 30
# Begrenzter Retry fuer publish_message(): faengt kurze Aussetzer/einen
# RabbitMQ-Neustart waehrend eines einzelnen Publish-Versuchs ab, ohne einen
# HTTP-Request oder den Consumer-Thread bei einem laengeren Ausfall
# unbegrenzt zu blockieren (dafuer ist consume_messages()/_connect_with_retry
# mit ihrem unbegrenzten Backoff gedacht).
_PUBLISH_MAX_ATTEMPTS = 3
_PUBLISH_RETRY_BACKOFF_SECONDS = 1.0


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
    """Veroeffentlicht eine Nachricht auf dem Exchange, mit kurzem Retry bei Ausfall.

    Wiederholt bis zu _PUBLISH_MAX_ATTEMPTS Mal mit kurzem Backoff, falls
    RabbitMQ gerade nicht erreichbar ist (z.B. mitten in einem Neustart).
    Bleibt der Ausfall laenger bestehen, wird die letzte Exception nach dem
    letzten Versuch weitergereicht - kein Ersatz fuer den unbegrenzten
    Reconnect-Loop der Consumer-Seite (_connect_with_retry).
    """
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
            # OSError zusaetzlich: siehe Kommentar in _connect_with_retry() -
            # DNS-Aufloesung des RabbitMQ-Hostnamens kann waehrend eines
            # Neustarts als roher socket.gaierror durchschlagen.
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
    """Verbindet mit RabbitMQ, mit Backoff-Retry bei Start-/Verbindungsproblemen.

    Gibt None zurueck, wenn stop_event waehrend des Wartens gesetzt wurde
    (sauberer Shutdown statt Endlosschleife).
    """
    delay = _INITIAL_RECONNECT_DELAY_SECONDS
    while not stop_event.is_set():
        try:
            parameters = pika.URLParameters(settings.rabbitmq_url)
            return pika.BlockingConnection(parameters)
        except (pika.exceptions.AMQPConnectionError, OSError) as exc:
            # OSError zusaetzlich: schon die DNS-Aufloesung des RabbitMQ-
            # Hostnamens (z.B. waehrend eines Container-Neustarts) kann als
            # roher socket.gaierror durchschlagen, den pika NICHT in
            # AMQPConnectionError wrapt - ohne diesen Fang stirbt der
            # Consumer-Thread bei diesem Szenario endgueltig.
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
            channel.queue_declare(queue=WAREHOUSE_QUEUE_NAME, durable=True)
            for routing_key in routing_keys:
                channel.queue_bind(
                    queue=WAREHOUSE_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=routing_key
                )

            for method_frame, properties, body in channel.consume(
                    WAREHOUSE_QUEUE_NAME,
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
                    logger.exception("Failed to handle warehouse message")
                    channel.basic_nack(method_frame.delivery_tag, requeue=False)
        except (pika.exceptions.AMQPError, OSError) as exc:
            # OSError zusaetzlich: aus Konsistenz mit _connect_with_retry() -
            # falls auch bei bestehender Verbindung ein roher Socket-Fehler
            # statt einer gewrappten AMQP-Exception durchschlaegt.
            # AMQPError ist die gemeinsame Basisklasse ALLER pika-Fehler
            # (Connection- UND Channel-bezogen) - eine schmalere Liste
            # einzelner Exception-Typen liesse den Consumer-Thread bei einer
            # nicht explizit gelisteten Variante (z.B. ChannelWrongStateError,
            # wenn RabbitMQ genau waehrend eines publish_message()-Aufrufs
            # ausfaellt und der anschliessende basic_nack() ebenfalls fehlt-
            # schlaegt) lautlos sterben, ohne sich je wieder zu verbinden.
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
