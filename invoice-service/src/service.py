"""Service-Schicht des invoice-service.

Enthaelt die Business-Logik fuer das RabbitMQ-Command-Handling
(handle_invoice_message()) sowie die Umwandlung eines DB-Rows in die
API-Antwortform (_serialize_invoice()), die von routes.py genutzt wird.
Die eigentliche PDF-Erzeugung liegt getrennt in pdf.py.
"""

import logging
from uuid import uuid4

from .database import mark_invoice_created, mark_invoice_failed, upsert_invoice_processing
from .messaging import build_message, publish_message
from .pdf import create_invoice_pdf

logger = logging.getLogger(__name__)


def handle_invoice_message(message: dict) -> None:
    """Verarbeitet EIN "invoice.create.requested"-Command mit genau einem Versuch.

    Die Retry-Orchestrierung liegt in der Shop-Saga, da nur shop-service den
    Zustand des zugehoerigen Circuit Breakers kennt. Schlaegt die
    Rechnungserstellung fehl, wird nach genau einem Versuch
    "invoice.failed" veroeffentlicht - inklusive "attempt" und der fachlichen
    Zahlungsdaten (provider/amount/currency/scenario), damit shop-service daraus
    bei Bedarf einen neuen "invoice.create.requested" mit attempt+1 bauen kann,
    ohne dass invoice-service selbst Retry-Zustand halten muss.
    """
    if message["type"] != "invoice.create.requested":
        return

    payload = message.get("payload", {})
    invoice_id = str(uuid4())
    # attempt kommt von shop-service (1 beim Erstversuch, sonst hochgezaehlt) -
    # invoice-service nutzt den Wert nur zum Protokollieren/Speichern, nicht
    # fuer eigene Retry-Entscheidungen.
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
    """Wandelt einen DB-Row-Dict in die API-Antwortform um (UUIDs als str,
    abgeleitete downloadUrl nur wenn ueberhaupt eine PDF existiert)."""
    invoice_id = str(invoice["invoiceId"])
    return {
        **invoice,
        "invoiceId": invoice_id,
        "orderId": str(invoice["orderId"]),
        "correlationId": str(invoice["correlationId"]),
        "downloadUrl": f"/invoices/{invoice_id}/pdf" if invoice.get("pdfPath") else None,
    }
