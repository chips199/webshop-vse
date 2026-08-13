"""Datenbankzugriff des invoice-service.

Eine Tabelle (invoices) mit einem Datensatz je Rechnungs-Versuch: seit der
Umstellung auf "ein Versuch pro invoice.create.requested" (siehe main.py)
erzeugt jeder Versuch eine eigene invoice_id/Zeile statt sie ueber mehrere
interne Retries hinweg wiederzuverwenden.
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings


CREATE_INVOICES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL,
    correlation_id UUID NOT NULL,
    transaction_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    pdf_path TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoices_order_id
    ON invoices (order_id);
"""


def init_database() -> None:
    """Legt Tabelle/Index an, falls sie noch nicht existieren (idempotent).

    Wird beim Start von main.py (lifespan()) aufgerufen.
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            cursor.execute(CREATE_INVOICES_TABLE_SQL)


def upsert_invoice_processing(
    invoice_id: str,
    correlation_id: str,
    payload: dict[str, Any],
    attempt: int,
) -> None:
    """Legt den Verarbeitungs-Datensatz fuer EINEN Rechnungs-Versuch an.

    Wird in handle_invoice_message() als allererstes aufgerufen, noch bevor
    das PDF gerendert wird - so ist der Versuch nachvollziehbar
    protokolliert, selbst wenn die PDF-Erzeugung danach fehlschlaegt.
    "ON CONFLICT ... DO UPDATE" macht den Aufruf idempotent (schadet nicht,
    falls dieselbe invoice_id aus irgendeinem Grund zweimal reinkommt),
    obwohl invoice_id in der Praxis pro Versuch neu per uuid4() erzeugt wird.
    status="RETRYING" ab attempt > 1 ist rein informativ fuer die Anzeige/
    Fehlersuche - beeinflusst kein Verhalten hier im Service.
    """
    query = """
    INSERT INTO invoices (
        id, order_id, correlation_id, transaction_id, provider, amount, currency, status, attempts
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        status = EXCLUDED.status,
        attempts = EXCLUDED.attempts,
        updated_at = now();
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    invoice_id,
                    payload["orderId"],
                    correlation_id,
                    payload["transactionId"],
                    payload["provider"],
                    payload["amount"],
                    payload["currency"],
                    "RETRYING" if attempt > 1 else "PROCESSING",
                    attempt,
                ),
            )


def mark_invoice_created(invoice_id: str, pdf_path: str, attempts: int) -> None:
    """Markiert einen Rechnungs-Versuch als erfolgreich (PDF wurde erzeugt)."""
    query = """
    UPDATE invoices
    SET status = %s, pdf_path = %s, attempts = %s, last_error = NULL, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, ("CREATED", pdf_path, attempts, invoice_id))


def mark_invoice_failed(invoice_id: str, error: str, attempts: int) -> None:
    """Markiert einen Rechnungs-Versuch als fehlgeschlagen (siehe invoice.failed
    in main.py) - der Retry fuer die naechste attempt-Nummer laeuft ueber
    eine neue invoice_id/Zeile, nicht ueber ein Update dieses Datensatzes."""
    query = """
    UPDATE invoices
    SET status = %s, attempts = %s, last_error = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, ("FAILED", attempts, error, invoice_id))


def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    """Liest eine einzelne Rechnung fuer die GET-Endpunkte in main.py aus."""
    query = """
    SELECT
        id AS "invoiceId",
        order_id AS "orderId",
        correlation_id AS "correlationId",
        status,
        pdf_path AS "pdfPath",
        attempts,
        last_error AS "lastError"
    FROM invoices
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (invoice_id,))
            return cursor.fetchone()
