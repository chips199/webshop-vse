"""Rechnungserstellung des Invoice-Service."""

import logging
from uuid import uuid4

from .database import mark_invoice_created, mark_invoice_failed, upsert_invoice_processing
from .messaging import build_message, publish_message
from .pdf import create_invoice_pdf

logger = logging.getLogger(__name__)


def handle_invoice_message(message: dict) -> None:
    """Verarbeitet einen einzelnen Rechnungsversuch."""
    if message["type"] != "invoice.create.requested":
        return

    payload = message.get("payload", {})
    invoice_id = str(uuid4())
    # Die Shop-Saga verwaltet die Versuchsnummer.
    attempt = payload.get("attempt", 1)

    try:
        upsert_invoice_processing(invoice_id, message["correlationId"], payload, attempt)
        if payload.get("scenario") == "invoice_failed":
            raise RuntimeError("Rechnungserstellung wurde fuer das Fehlerszenario gezielt abgelehnt.")

        invoice_path = create_invoice_pdf(invoice_id, message["correlationId"], payload)
        mark_invoice_created(invoice_id, str(invoice_path), attempt)
        event = build_message(
            "invoice.created",
            message["correlationId"],
            {
                "invoiceId": invoice_id,
                "orderId": payload["orderId"],
                "transactionId": payload["transactionId"],
                "status": "CREATED",
                "pdfPath": str(invoice_path),
                "attempts": attempt,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("invoice.created", event)
    except Exception as exc:
        last_error = str(exc)
        logger.warning(
            "Invoice creation attempt failed",
            extra={
                "correlation_id": message["correlationId"],
                "context": {"orderId": payload.get("orderId"), "attempt": attempt, "error": last_error},
            },
        )
        mark_invoice_failed(invoice_id, last_error, attempt)
        failed_event = build_message(
            "invoice.failed",
            message["correlationId"],
            {
                "invoiceId": invoice_id,
                "orderId": payload["orderId"],
                "transactionId": payload["transactionId"],
                "provider": payload.get("provider"),
                "amount": payload.get("amount"),
                "currency": payload.get("currency"),
                "scenario": payload.get("scenario", "happy_path"),
                "attempt": attempt,
                "reasonCode": "INVOICE_RENDER_FAILED",
                "message": "Rechnungserstellung fehlgeschlagen.",
                "lastError": last_error,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("invoice.failed", failed_event)


def _serialize_invoice(invoice: dict) -> dict:
    """Serialisiert einen Rechnungsdatensatz fuer die API."""
    invoice_id = str(invoice["invoiceId"])
    return {
        **invoice,
        "invoiceId": invoice_id,
        "orderId": str(invoice["orderId"]),
        "correlationId": str(invoice["correlationId"]),
        "downloadUrl": f"/invoices/{invoice_id}/pdf" if invoice.get("pdfPath") else None,
    }
