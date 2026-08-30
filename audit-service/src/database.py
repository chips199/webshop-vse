"""Datenbankzugriff des audit-service.

Genau eine Tabelle (audit_snapshots), ausschliesslich INSERT und SELECT -
niemals UPDATE oder DELETE. Das ist die technische Grundlage fuer
Event Sourcing Light: die Tabelle ist ein Append-Only-Log, der aktuelle
Zustand einer Bestellung ergibt sich erst durch das Lesen/Zusammensetzen
aller ihrer Snapshots (siehe get_snapshots_by_correlation_id()).
"""

from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings


# previous_event_id ist bewusst NULLable (nicht jeder Snapshot hat einen
# Vorgaenger, z.B. der jeweils erste Snapshot einer neuen correlationId) und
# OHNE Fremdschluessel-Constraint auf id: die referenzierte Nachricht kann aus
# einem anderen Service stammen und ist zum Zeitpunkt des Inserts hier evtl.
# noch nicht bekannt/gespeichert - ein FK wuerde Inserts unnoetig verkomplizieren
# oder in falscher Reihenfolge sogar verhindern.
CREATE_AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    service TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    previous_event_id UUID NULL,
    actor TEXT NOT NULL,
    status_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_snapshots_correlation_timestamp
    ON audit_snapshots (correlation_id, timestamp);
"""


def init_database() -> None:
    """Legt Tabelle/Index an, falls sie noch nicht existieren (idempotent).

    Wird beim Start von main.py (lifespan()) aufgerufen. pgcrypto liefert
    gen_random_uuid() fuer den Primaerschluessel-Default der Tabelle.
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            cursor.execute(CREATE_AUDIT_TABLE_SQL)


def get_snapshots_by_correlation_id(correlation_id: str) -> Iterable[dict[str, Any]]:
    """Liefert alle Snapshots einer Bestellung chronologisch sortiert.

    Wird vom Endpunkt GET /audit/orders/{correlationId} verwendet.
    ORDER BY timestamp, created_at: timestamp stammt aus
    der urspruenglichen Nachricht (kann bei zwei Events in derselben
    Millisekunde gleich sein), created_at ist der DB-Insert-Zeitpunkt und
    sorgt in diesem Fall fuer eine stabile, wirklich chronologische
    Reihenfolge.
    """
    query = """
    SELECT
        id,
        correlation_id AS "correlationId",
        event_type AS "eventType",
        service,
        timestamp,
        payload,
        previous_event_id AS "previousEventId",
        actor,
        status_code AS "statusCode"
    FROM audit_snapshots
    WHERE correlation_id = %s
    ORDER BY timestamp ASC, created_at ASC;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (correlation_id,))
            return cursor.fetchall()


def insert_snapshot_from_message(message: dict[str, Any]) -> None:
    """Persistiert EINE RabbitMQ-Nachricht als unveraenderlichen Audit-Snapshot.

    Wird von messaging.py fuer jede konsumierte Nachricht aufgerufen (die
    Queue ist ueber Routing-Key "#" an ALLE Nachrichten auf dem Exchange
    gebunden - audit-service filtert bewusst nicht nach Nachrichtentyp,
    siehe Modul-Docstring). Die messageId der urspruenglichen Nachricht wird
    direkt als Snapshot-id verwendet: "ON CONFLICT (id) DO NOTHING" macht den
    Insert idempotent, falls dieselbe Nachricht (z.B. nach einem RabbitMQ-
    Requeue) zweimal ankommt - es entsteht kein doppelter Snapshot, und es
    ist explizit kein UPDATE, das die Unveraenderlichkeit verletzen wuerde.
    """
    query = """
    INSERT INTO audit_snapshots (
        id,
        correlation_id,
        event_type,
        service,
        timestamp,
        payload,
        previous_event_id,
        actor,
        status_code
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING;
    """
    message_type = message["type"]
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    message["messageId"],
                    message["correlationId"],
                    # eventType wird direkt aus dem Nachrichtentyp abgeleitet,
                    # z.B. "billing.payment.failed" -> "BILLING_PAYMENT_FAILED" -
                    # es gibt keine separate Liste erlaubter Event-Typen, jede
                    # Nachricht auf dem Exchange wird 1:1 zu einem Snapshot.
                    message_type.upper().replace(".", "_"),
                    message["sourceService"],
                    message["timestamp"],
                    # Jsonb() statt json.dumps(): laesst psycopg das Python-
                    # dict direkt und typsicher in die JSONB-Spalte schreiben.
                    Jsonb(message.get("payload", {})),
                    message.get("previousEventId"),
                    # actor = sourceService: audit-service kennt keine
                    # einzelnen Benutzer/Akteure, nur den Service, der die
                    # Nachricht veroeffentlicht hat.
                    message["sourceService"],
                    _status_code_for(message_type),
                ),
            )


def _status_code_for(message_type: str) -> str:
    """Leitet das statusCode-Feld (SUCCESS/FAILURE/...) rein aus
    dem Nachrichtentyp-String ab - bewusst nur Substring-Muster, keine feste
    Liste aller Typen, damit neue Event-Typen in anderen Services nicht auch
    noch hier nachgepflegt werden muessen (audit-service bleibt generisch
    und ohne Business-Wissen, siehe Modul-Docstring oben).
    """
    if ".failed" in message_type:
        return "FAILURE"
    if ".retry." in message_type:
        return "RETRY"
    if ".cancel." in message_type or ".refund." in message_type:
        # Kompensations-Commands laufen gerade erst an ("...requested") oder
        # sind noch in der Zwischenphase - COMPENSATING statt SUCCESS/FAILURE,
        # weil die Zahlung/Reservierung erst durch das Ergebnis dieses
        # Schritts endgueltig storniert bzw. erstattet ist.
        return "COMPENSATING"
    if "rollback.completed" in message_type:
        return "COMPENSATED"
    return "SUCCESS"
