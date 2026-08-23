"""Saga-Service-Schicht des shop-service: RabbitMQ-Event-Handling (Choreografie).

Enthaelt die zentrale Verteil-/Entscheidungslogik der Shop-Saga
(handle_saga_message()) sowie alle Hilfsfunktionen, die dabei gebraucht
werden: Echtzeit-Benachrichtigung des Admin-Dashboards, den
Circuit-Breaker-gated Rechnungs-Aufruf samt Retry-Orchestrierung
(Bonusaufgabe 4.1) und die Vollstaendigkeitspruefung fuer order.completed.
Die zwei HTTP-Endpunkte, die ebenfalls einen Saga-Schritt anstossen
(create_order(), confirm_order_payment() in routes.py), rufen
notify_admin_dashboard() direkt von hier auf.
"""

import logging
import threading

from . import realtime
from .config import settings
from .database import (
    complete_order_if_ready,
    get_order as get_order_record,
    update_invoice_created,
    update_order_status,
    update_payment_action_required,
    update_payment_succeeded,
    update_warehouse_commit,
)
from .messaging import build_message, publish_message
from .resilience import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)

invoice_circuit_breaker = CircuitBreaker(
    failure_threshold=settings.invoice_circuit_breaker_failure_threshold,
    reset_seconds=settings.invoice_circuit_breaker_reset_seconds,
    half_open_max_calls=settings.invoice_circuit_breaker_half_open_max_calls,
)


def handle_saga_message(message: dict) -> None:
    """Zentraler Verteiler fuer alle eingehenden Saga-Events (Choreografie).

    shop-service konsumiert praktisch jedes Event, das die anderen Services
    publizieren, und entscheidet hier - abhaengig vom message_type - was als
    naechstes zu tun ist (naechstes Command publizieren, Bestellstatus
    aktualisieren, oder beides). Jeder Zweig endet mit `return`, sobald er
    fertig ist. Reihenfolge der Zweige entspricht grob dem Happy-Path-Ablauf
    der Saga; Fehler-/Kompensationszweige (z.B. billing.payment.failed,
    warehouse.commit.failed) stossen jeweils die passende
    Rueckabwicklung an.
    """
    message_type = message["type"]
    payload = message.get("payload", {})
    correlation_id = message["correlationId"]

    # Admin-Dashboard-Echtzeit-Update (Bonusaufgabe 4.3): bewusst EIN
    # generischer Hook direkt hier statt eines eigenen Aufrufs in jedem der
    # vielen if-Zweige unten - fast jede Saga-Nachricht traegt eine orderId
    # im Payload, und dem Dashboard reicht "fuer diese Bestellung hat sich
    # etwas getan, bitte neu laden" (siehe realtime.py). Laeuft im
    # Consumer-Thread und darf daher nicht blockieren - notify_admin_dashboard
    # faengt eigene Fehler ab, damit ein Problem beim Publizieren die
    # eigentliche Saga-Verarbeitung niemals stoeren kann.
    notify_admin_dashboard(payload.get("orderId"), correlation_id, message_type)

    if message_type == "warehouse.reservation.succeeded":
        order_id = payload["orderId"]
        update_order_status(order_id, "RESERVED")
        payment_requested = build_message(
            "billing.payment.requested",
            correlation_id,
            {
                "orderId": order_id,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "provider": payload["provider"],
                "scenario": payload.get("scenario", "happy_path"),
                "payment": payload.get("payment", {}),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.requested", payment_requested)
        update_order_status(order_id, "PAYMENT_PENDING")
        return

    if message_type == "warehouse.reservation.failed":
        update_order_status(payload["orderId"], "OUT_OF_STOCK")
        return

    if message_type == "billing.payment.pending":
        redirect_url = payload.get("redirectUrl")
        if redirect_url:
            update_payment_action_required(payload["orderId"], payload["transactionId"], redirect_url)
        return

    if message_type == "billing.payment.succeeded":
        order_id = payload["orderId"]
        update_payment_succeeded(
            order_id,
            payload["transactionId"],
            customer=payload.get("customer"),
            shipping_address=payload.get("shippingAddress"),
        )
        request_invoice_with_circuit(order_id, correlation_id, payload, message)

        commit_requested = build_message(
            "warehouse.commit.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "scenario": payload.get("scenario", "happy_path"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.commit.requested", commit_requested)
        return

    if message_type == "billing.payment.failed":
        order_id = payload["orderId"]
        update_order_status(order_id, "PAYMENT_FAILED")
        cancel_requested = build_message(
            "warehouse.cancel.requested",
            correlation_id,
            {
                "orderId": order_id,
                "reasonCode": payload.get("reasonCode", "PAYMENT_FAILED"),
                "message": "Zahlung fehlgeschlagen, Warehouse-Reservierung wird storniert.",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.cancel.requested", cancel_requested)
        return

    if message_type == "warehouse.cancel.succeeded":
        update_order_status(payload["orderId"], "PAYMENT_FAILED")
        return

    if message_type == "invoice.created":
        order_id = payload["orderId"]
        transition = invoice_circuit_breaker.record_success()
        publish_invoice_circuit_transition(correlation_id, order_id, transition, message["messageId"])
        update_invoice_created(order_id, payload["invoiceId"])
        maybe_publish_order_completed(order_id, correlation_id, message)
        return

    if message_type == "invoice.failed":
        order_id = payload["orderId"]
        # attempt kommt von invoice-service durchgereicht (siehe dortiges
        # handle_invoice_message) - Default 1 nur zur Absicherung, falls eine
        # aeltere/fremde Nachricht ohne dieses Feld hereinkommt.
        attempt = payload.get("attempt", 1)
        transition = invoice_circuit_breaker.record_failure(payload.get("reasonCode", "INVOICE_FAILED"))
        publish_invoice_circuit_transition(correlation_id, order_id, transition, message["messageId"])

        if attempt < settings.invoice_max_retries:
            # Noch Versuche uebrig: Bestellung bleibt (sichtbar) im Retry-Zustand,
            # und die Shop-Saga plant selbst den naechsten Versuch (siehe
            # schedule_invoice_retry) - invoice-service haelt dafuer keinen
            # eigenen Zustand mehr.
            update_order_status(order_id, "INVOICE_RETRY_PENDING")
            schedule_invoice_retry(order_id, correlation_id, payload, message, attempt)
        else:
            # Alle Versuche verbraucht: endgueltiger, nicht mehr automatisch
            # wiederholter Fehlerzustand - bewusst von INVOICE_RETRY_PENDING
            # unterschieden, damit im Admin-Frontend erkennbar ist, dass hier
            # kein weiterer Versuch mehr folgt.
            update_order_status(order_id, "INVOICE_FAILED")
            logger.error(
                "Invoice creation failed permanently after exhausting retries",
                extra={
                    "correlation_id": correlation_id,
                    "context": {"orderId": order_id, "attempt": attempt, "maxAttempts": settings.invoice_max_retries},
                },
            )
        return

    if message_type == "warehouse.commit.succeeded":
        order_id = payload["orderId"]
        update_warehouse_commit(order_id, "SUCCEEDED")
        maybe_publish_order_completed(order_id, correlation_id, message)
        return

    if message_type == "warehouse.commit.failed":
        order_id = payload["orderId"]
        update_order_status(order_id, "REFUND_PENDING")
        refund_requested = build_message(
            "billing.refund.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "reasonCode": payload.get("reasonCode", "WAREHOUSE_COMMIT_FAILED"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.refund.requested", refund_requested)
        return

    if message_type == "billing.refund.succeeded":
        order_id = payload["orderId"]
        update_order_status(order_id, "ROLLBACK_COMPLETED")
        rollback_completed = build_message(
            "order.rollback.completed",
            correlation_id,
            {
                "orderId": order_id,
                "status": "ROLLBACK_COMPLETED",
                "transactionId": payload["transactionId"],
            },
            previous_event_id=message["messageId"],
        )
        publish_message("order.rollback.completed", rollback_completed)
        return

    if message_type == "billing.refund.failed":
        update_order_status(payload["orderId"], "REFUND_FAILED")


def notify_admin_dashboard(order_id: str | None, correlation_id: str | None, reason: str) -> None:
    """Benachrichtigt alle verbundenen Admin-Dashboards ueber eine Aenderung.

    `reason` ist der ausloesende Nachrichtentyp (z.B. "billing.payment.
    succeeded") bzw. "order.created"/"order.payment-confirmation" fuer die
    beiden HTTP-Aufrufstellen in routes.py - nur zu Debugging-/Logging-Zwecken
    im Frontend, keine fachliche Bedeutung fuer den Consumer der
    SSE-Verbindung (siehe admin_orders_events() in routes.py / realtime.py):
    das Dashboard reagiert einheitlich mit "betroffene Bestellung neu laden",
    unabhaengig vom genauen Grund.
    """
    if not order_id:
        return
    try:
        realtime.publish({"orderId": str(order_id), "correlationId": str(correlation_id), "reason": reason})
    except Exception:
        # Darf die eigentliche Verarbeitung (Saga-Consumer oder HTTP-Request)
        # niemals stoeren - das Live-Update ist ein Komfortfeature, kein
        # fachlich kritischer Pfad.
        logger.warning("Failed to publish admin dashboard realtime event", extra={"context": {"orderId": order_id}})


def maybe_publish_order_completed(order_id: str, correlation_id: str, previous_message: dict) -> None:
    """Prueft, ob alle drei Saga-Zweige (Zahlung/Rechnung/Lager) fertig sind,
    und publiziert order.completed nur dann - siehe complete_order_if_ready()
    in database.py fuer die eigentliche (atomare) Bedingung. Wird nach JEDEM
    der drei moeglichen Abschluss-Events aufgerufen (invoice.created,
    warehouse.commit.succeeded), da die Reihenfolge nicht feststeht."""
    if not complete_order_if_ready(order_id):
        return
    order_completed = build_message(
        "order.completed",
        correlation_id,
        {
            "orderId": order_id,
            "status": "COMPLETED",
        },
        previous_event_id=previous_message["messageId"],
    )
    publish_message("order.completed", order_completed)


def request_invoice_with_circuit(
    order_id: str,
    correlation_id: str,
    payload: dict,
    previous_message: dict,
    attempt: int = 1,
) -> None:
    """Fordert eine Rechnung an - Circuit-Breaker-gated, mit Versuchsnummer.

    `attempt` ist 1 beim allerersten Versuch (ausgeloest durch
    billing.payment.succeeded) und wird bei Wiederholungen von
    schedule_invoice_retry() hochgezaehlt durchgereicht. invoice-service
    bekommt den Wert im Payload mit, damit es ihn beim Melden eines
    Fehlschlags unveraendert zurueckspiegeln kann (siehe invoice.failed-Zweig
    oben) - so muss der Retry-Zaehler an keiner Stelle dauerhaft
    gespeichert werden.
    """
    try:
        transition = invoice_circuit_breaker.before_call()
        publish_invoice_circuit_transition(correlation_id, order_id, transition, previous_message["messageId"])
    except CircuitBreakerOpenError as exc:
        update_order_status(order_id, "INVOICE_FAILED")
        logger.warning(
            "Invoice request blocked by circuit breaker",
            extra={"correlation_id": correlation_id, "context": {"orderId": order_id, "error": str(exc)}},
        )
        return

    order = get_order_record(order_id) or {}
    invoice_payload = {
        "orderId": order_id,
        "transactionId": payload["transactionId"],
        "provider": payload["provider"],
        "amount": payload["amount"],
        "currency": payload["currency"],
        "scenario": payload.get("scenario", "happy_path"),
        "customer": order.get("customer") or {},
        "shippingAddress": order.get("shippingAddress") or {},
        "billingAddress": order.get("billingAddress"),
        "items": order.get("items") or [],
        "attempt": attempt,
    }
    invoice_requested = build_message(
        "invoice.create.requested",
        correlation_id,
        invoice_payload,
        previous_event_id=previous_message["messageId"],
    )
    publish_message("invoice.create.requested", invoice_requested)


def schedule_invoice_retry(
    order_id: str,
    correlation_id: str,
    payload: dict,
    previous_message: dict,
    attempt: int,
) -> None:
    """Plant nach kurzem Backoff einen weiteren Rechnungs-Versuch.

    Diese Retry-Orchestrierung sitzt bewusst hier in der Shop-Saga und nicht
    mehr in invoice-service: nur shop-service kennt den Zustand des
    Circuit Breakers fuer Invoice-Aufrufe (Bonusaufgabe 4.1) und damit, ob ein
    weiterer Versuch aktuell ueberhaupt sinnvoll ist. Der eigentliche
    Retry-Aufruf laeuft in einem eigenen Timer-Thread ab (wie z.B. auch der
    verzoegerte Webhook in billing-service), damit der Nachrichten-Consumer
    hier nicht blockiert wird.

    `payload` ist das Payload der invoice.failed-Nachricht und enthaelt
    bereits transactionId/provider/amount/currency/scenario - alles, was
    request_invoice_with_circuit() braucht (Kunden-/Lieferdaten werden dort
    ohnehin frisch per get_order_record() nachgeladen statt hier
    mitgeschleppt zu werden).
    """
    next_attempt = attempt + 1
    retry_event = build_message(
        "invoice.retry.scheduled",
        correlation_id,
        {
            "orderId": order_id,
            "transactionId": payload.get("transactionId"),
            "attempt": next_attempt,
            "maxAttempts": settings.invoice_max_retries,
            "reasonCode": payload.get("reasonCode", "INVOICE_RENDER_FAILED"),
            "message": payload.get("message"),
        },
        previous_event_id=previous_message["messageId"],
    )
    publish_message("invoice.retry.scheduled", retry_event)

    delay_seconds = settings.invoice_retry_backoff_seconds * attempt
    timer = threading.Timer(
        delay_seconds,
        request_invoice_with_circuit,
        kwargs={
            "order_id": order_id,
            "correlation_id": correlation_id,
            "payload": payload,
            "previous_message": retry_event,
            "attempt": next_attempt,
        },
    )
    timer.daemon = True
    timer.start()


def publish_invoice_circuit_transition(
    correlation_id: str,
    order_id: str | None,
    transition,
    previous_event_id: str,
) -> None:
    """Meldet eine CircuitTransition (falls vorhanden) als eigenes Event, damit
    Zustandswechsel des Invoice-Circuit-Breakers ueber die Audit-Snapshots
    nachvollziehbar sind. `transition` ist None, wenn before_call()/
    record_success()/record_failure() keinen Zustandswechsel ausgeloest
    haben - dann wird bewusst nichts publiziert."""
    if transition is None:
        return
    event = build_message(
        "invoice.circuit.state.changed",
        correlation_id,
        {
            "circuitName": "invoice-service",
            "orderId": order_id,
            "previousState": transition.previous_state.value,
            "state": transition.state.value,
            "failureCount": transition.failure_count,
            "reasonCode": transition.reason,
        },
        previous_event_id=previous_event_id,
    )
    publish_message("invoice.circuit.state.changed", event)
