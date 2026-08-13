"""RabbitMQ-Anbindung des audit-service.

Anders als bei den anderen Services gibt es hier kein publish_message() -
audit-service veroeffentlicht selbst nie Nachrichten, sondern konsumiert nur
(siehe consume_audit_events() unten, gebunden auf Routing-Key "#" statt auf
einzelne Event-Typen wie bei den anderen Services).
"""

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import pika
import pika.exceptions

from .config import settings

# Topic-Exchange, ueber den alle Services ihre Events/Commands austauschen.
EXCHANGE_NAME = "webshop.events"
# Eigene Queue des audit-service - gebunden auf "#" (siehe queue_bind()
# unten), bekommt dadurch als einziger Service wirklich JEDE Nachricht.
AUDIT_QUEUE_NAME = "audit-service.snapshots"

logger = logging.getLogger(__name__)

_INITIAL_RECONNECT_DELAY_SECONDS = 2
_MAX_RECONNECT_DELAY_SECONDS = 30


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


def consume_audit_events(
        handle_message: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
) -> None:
    """Konsumiert dauerhaft ALLE Nachrichten auf dem Exchange (Routing-Key "#").

    Blockierend - gedacht zum Laufen in einem eigenen Thread (siehe
    lifespan() in main.py). Fuer jede empfangene Nachricht wird
    `handle_message(message)` aufgerufen (im Normalbetrieb
    insert_snapshot_from_message aus database.py); wirft der Handler eine
    Exception, wird die Nachricht negativ bestaetigt (nack, ohne Requeue)
    statt den ganzen Consumer abstuerzen zu lassen. `stop_event` erlaubt
    einen sauberen Shutdown von aussen.
    """
    # Aeussere Schleife: baut die Verbindung neu auf, sobald sie einmal
    # verloren geht (Start-Race, RabbitMQ-Neustart, Netzwerk-Hiccup, ...).
    while not stop_event.is_set():
        connection = _connect_with_retry(stop_event)
        if connection is None:
            return  # stop_event wurde gesetzt, bevor eine Verbindung stand

        channel = None
        try:
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=AUDIT_QUEUE_NAME, durable=True)
            # routing_key="#" ist bei einem Topic-Exchange der Alles-Platzhalter
            # (matcht jeden Routing-Key, beliebig viele Wortsegmente) - genau
            # das macht audit-service zum generischen Event-Sink ohne Wissen
            # ueber einzelne Nachrichtentypen.
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
            # OSError zusaetzlich: aus Konsistenz mit _connect_with_retry() -
            # falls auch bei bestehender Verbindung ein roher Socket-Fehler
            # statt einer gewrappten AMQP-Exception durchschlaegt.
            # AMQPError ist die gemeinsame Basisklasse ALLER pika-Fehler
            # (Connection- UND Channel-bezogen) - eine schmalere Liste
            # einzelner Exception-Typen liesse den Consumer-Thread bei einer
            # nicht explizit gelisteten Variante (z.B. ChannelWrongStateError)
            # lautlos sterben, ohne sich je wieder zu verbinden.
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
