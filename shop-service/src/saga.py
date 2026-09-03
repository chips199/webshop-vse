"""Ereignisverarbeitung der Shop-Saga."""

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
    """Verarbeitet eingehende Saga-Ereignisse und startet Folgeschritte."""
    message_type = message["type"]
    payload = message.get("payload", {})
    correlation_id = message["correlationId"]

    # Einheitliches Aenderungssignal fuer das Admin-Dashboard.
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
        # Nachrichten ohne Versuchsnummer gelten als erster Versuch.
        attempt = payload.get("attempt", 1)
        transition = invoice_circuit_breaker.record_failure(payload.get("reasonCode", "INVOICE_FAILED"))
        publish_invoice_circuit_transition(correlation_id, order_id, transition, message["messageId"])

        if attempt < settings.invoice_max_retries:
            update_order_status(order_id, "INVOICE_RETRY_PENDING")
            schedule_invoice_retry(order_id, correlation_id, payload, message, attempt)
        else:
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
    """Sendet ein Aenderungssignal an verbundene Admin-Dashboards."""
    if not order_id:
        return
    try:
        realtime.publish({"orderId": str(order_id), "correlationId": str(correlation_id), "reason": reason})
    except Exception:
        # Fehler im Live-Update duerfen die Saga nicht abbrechen.
        logger.warning("Failed to publish admin dashboard realtime event", extra={"context": {"orderId": order_id}})


def maybe_publish_order_completed(order_id: str, correlation_id: str, previous_message: dict) -> None:
    """Publiziert den Abschluss nach Zahlung, Rechnung und Lager-Commit."""
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
    """Fordert eine Rechnung unter Kontrolle des Circuit Breakers an."""
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
    """Plant einen weiteren Rechnungsversuch mit linearem Backoff."""
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
    """Publiziert einen Zustandswechsel des Invoice-Circuit-Breakers."""
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
