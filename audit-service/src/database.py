from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row

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
