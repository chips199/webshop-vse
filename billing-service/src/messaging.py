"""RabbitMQ-Anbindung des billing-service.

Enthaelt alles, was mit dem Nachrichten-Broker zu tun hat: Verbindungsaufbau
mit Retry, Event-Umschlag (build_message), Publizieren (publish_message) und
den Consumer-Loop (consume_messages), der eingehende Commands an einen
Handler weiterreicht. Fachlicher Code (main.py) importiert nur diese
Funktionen und muss sich nie direkt mit pika beschaeftigen.
"""

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

# Topic-Exchange, ueber den alle Services (Shop/Billing/Warehouse/Invoice/
# Audit) ihre Events/Commands austauschen. Der Routing-Key entspricht dem
# jeweiligen Event-/Command-Typ (z.B. "billing.payment.requested").
EXCHANGE_NAME = "webshop.events"
# Queue, an die genau die fuer billing-service relevanten Routing-Keys
# gebunden werden (siehe consume_messages()-Aufruf in main.py).
BILLING_QUEUE_NAME = "billing-service.commands"

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
        except pika.exceptions.AMQPConnectionError:
            logger.warning(
                "RabbitMQ nicht erreichbar, naechster Verbindungsversuch in %ss",
                delay,
            )
            stop_event.wait(delay)
            delay = min(delay * 2, _MAX_RECONNECT_DELAY_SECONDS)
    return None


def build_message(
        message_type: str,
        correlation_id: str,
        payload: dict[str, Any],
        previous_event_id: str | None = None,
) -> dict[str, Any]:
    """Baut den einheitlichen Nachrichten-Umschlag fuer Events/Commands.

    Jede Nachricht bekommt eine eigene messageId, die Absender-Kennung
    (sourceService) und einen Zeitstempel automatisch mit - der Aufrufer
    liefert nur noch type, correlationId und die fachliche payload.
    previousEventId verknuepft die Nachricht mit dem Ereignis, das sie
    ausgeloest hat (fuer Nachvollziehbarkeit/Audit-Trail).
    """
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
    """Veroeffentlicht eine (mit build_message() gebaute) Nachricht auf dem Exchange.

    Oeffnet fuer jeden Aufruf eine eigene kurzlebige Verbindung - fuer den
    im Projekt ueblichen Nachrichtenumfang unkritisch, vermeidet aber
    Zustand/Threading-Probleme mit einer dauerhaft offenen Connection.
    "delivery_mode=Persistent" sorgt dafuer, dass die Nachricht einen
    RabbitMQ-Neustart uebersteht (Queue muss dafuer ebenfalls durable sein).
    """
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
    """Konsumiert dauerhaft Nachrichten fuer die gegebenen Routing-Keys.

    Blockierend - gedacht zum Laufen in einem eigenen Thread (siehe
    lifespan() in main.py). Fuer jede empfangene Nachricht wird
    `handle_message(payload)` aufgerufen; wirft der Handler eine Exception,
    wird die Nachricht negativ bestaetigt (nack, ohne Requeue) statt den
    ganzen Consumer abstuerzen zu lassen. `stop_event` erlaubt einen
    sauberen Shutdown von aussen (siehe lifespan()).
    """
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
            channel.queue_declare(queue=BILLING_QUEUE_NAME, durable=True)
            for routing_key in routing_keys:
                channel.queue_bind(
                    queue=BILLING_QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=routing_key
                )

            # auto_ack=False: wir bestaetigen Nachrichten erst manuell, NACHDEM
            # handle_message() erfolgreich durchgelaufen ist - geht beim
            # Verarbeiten etwas schief oder stuerzt der Prozess ab, bleibt die
            # Nachricht in der Queue und wird nicht stillschweigend verloren.
            # inactivity_timeout=1: channel.consume() blockiert nicht ewig,
            # sondern liefert alle 1s einen leeren Frame - so kann die
            # stop_event-Pruefung unten regelmaessig laufen (sauberer Shutdown).
            for method_frame, properties, body in channel.consume(
                    BILLING_QUEUE_NAME,
                    inactivity_timeout=1,
                    auto_ack=False,
            ):
                if stop_event.is_set():
                    break
                if method_frame is None:
                    continue  # Timeout ohne Nachricht - nur die Schleifenbedingung neu pruefen
                try:
                    handle_message(json.loads(body.decode("utf-8")))
                    channel.basic_ack(method_frame.delivery_tag)
                except Exception:
                    # requeue=False statt True: eine kaputte Nachricht wuerde
                    # sonst endlos neu zugestellt und immer wieder denselben
                    # Fehler ausloesen ("Poison Message"). Stattdessen wird
                    # sie verworfen und der Fehler geloggt.
                    logger.exception("Failed to handle billing message")
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
