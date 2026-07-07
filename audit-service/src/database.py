from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings


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
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            cursor.execute(CREATE_AUDIT_TABLE_SQL)


def get_snapshots_by_correlation_id(correlation_id: str) -> Iterable[dict[str, Any]]:
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
                    message_type.upper().replace(".", "_"),
                    message["sourceService"],
                    message["timestamp"],
                    Jsonb(message.get("payload", {})),
                    message.get("previousEventId"),
                    message["sourceService"],
                    _status_code_for(message_type),
                ),
            )


def _status_code_for(message_type: str) -> str:
    if ".failed" in message_type:
        return "FAILURE"
    if ".cancel." in message_type or ".refund." in message_type:
        return "COMPENSATING"
    if "rollback.completed" in message_type:
        return "COMPENSATED"
    return "SUCCESS"
