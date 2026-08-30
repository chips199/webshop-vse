"""RabbitMQ-Anbindung des billing-service.

Enthaelt alles, was mit dem Nachrichten-Broker zu tun hat: Verbindungsaufbau
mit Retry, Event-Umschlag (build_message), Publizieren (publish_message) und
den Consumer-Loop (consume_messages), der eingehende Commands an einen
Handler weiterreicht. Fachliche Module importieren nur diese Funktionen und
muessen sich nicht direkt mit pika beschaeftigen.
"""

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
# Begrenzter Retry fuer publish_message(): faengt kurze Aussetzer/einen
# RabbitMQ-Neustart waehrend eines einzelnen Publish-Versuchs ab, ohne einen
# HTTP-Request oder den Consumer-Thread bei einem laengeren Ausfall
# unbegrenzt zu blockieren (dafuer ist consume_messages()/_connect_with_retry
# mit ihrem unbegrenzten Backoff gedacht).
_PUBLISH_MAX_ATTEMPTS = 3
_PUBLISH_RETRY_BACKOFF_SECONDS = 1.0


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
            # OSError zusaetzlich zu AMQPConnectionError: schlaegt schon die
            # DNS-Aufloesung des RabbitMQ-Hostnamens fehl (z.B. weil der
            # RabbitMQ-Container gerade neu startet und Docker-DNS den Namen
            # kurzzeitig nicht auflösen kann), wirft pika intern einen rohen
            # socket.gaierror durch, OHNE ihn in AMQPConnectionError zu packen
            # (siehe pika.adapters.blocking_connection.
            # _reap_last_connection_workflow_error - nur
            # AMQPConnectorSocketConnectError wird gewrappt, ein Fehler in der
            # Resolve-Phase wird unveraendert durchgereicht). Ohne dieses
            # OSError hier wuerde der Consumer-Thread bei genau diesem
            # Szenario endgueltig sterben, statt es erneut zu versuchen.
            logger.warning(
                "RabbitMQ nicht erreichbar (%s), naechster Verbindungsversuch in %ss",
                exc,
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

    Wiederholt bis zu _PUBLISH_MAX_ATTEMPTS Mal mit kurzem Backoff, falls
    RabbitMQ gerade nicht erreichbar ist (z.B. mitten in einem Neustart) -
    ohne diesen Retry wuerde ein Aufrufer, der genau in diesem Moment
    publiziert, sofort eine unbehandelte Exception bekommen, obwohl ein
    zweiter Versuch Sekunden spaeter oft schon wieder klappt. Bleibt der
    Ausfall laenger bestehen, wird die letzte Exception nach dem letzten
    Versuch trotzdem weitergereicht - fuer einen laengeren Ausfall ist das
    hier bewusst kein Ersatz fuer den unbegrenzten Reconnect-Loop der
    Consumer-Seite.
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
            # OSError zusaetzlich zu AMQPError: siehe Kommentar in
            # _connect_with_retry() - eine DNS-Aufloesung des RabbitMQ-
            # Hostnamens kann waehrend eines Neustarts als roher
            # socket.gaierror durchschlagen, ohne von pika in eine eigene
            # AMQP-Exception gepackt zu werden.
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
        except (pika.exceptions.AMQPError, OSError) as exc:
            # OSError zusaetzlich zu AMQPError: aus Konsistenz mit
            # _connect_with_retry() - falls auch bei einer bereits
            # bestehenden Verbindung ein roher Socket-Fehler (statt einer
            # von pika gewrappten AMQP-Exception) durchschlaegt, soll der
            # Consumer-Thread trotzdem sauber neu verbinden statt zu sterben.
            # AMQPError ist die gemeinsame Basisklasse ALLER pika-Fehler -
            # sowohl verbindungsbezogener (AMQPConnectionError, StreamLostError,
            # ConnectionClosedByBroker, ...) als auch kanalbezogener
            # (ChannelClosedByBroker, ChannelWrongStateError, ...). Eine
            # schmalere Liste einzelner Exception-Typen hier ist ein bekannter
            # Stolperstein: faellt RabbitMQ z.B. genau waehrend eines
            # publish_message()-Aufrufs innerhalb von handle_message() aus,
            # kann der anschliessende channel.basic_nack()-Aufruf unten (im
            # inneren except Exception) selbst noch eine weitere, eventuell
            # nicht explizit gelistete pika-Exception werfen (z.B.
            # ChannelWrongStateError). Wird die nicht hier aufgefangen, stirbt
            # der Consumer-Thread lautlos und verbindet sich nie wieder neu -
            # auch nicht, wenn RabbitMQ anschliessend wieder erreichbar ist.
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
