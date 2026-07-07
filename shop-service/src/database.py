from collections.abc import Iterable
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings

CREATE_ORDERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shop_orders (
    id UUID PRIMARY KEY,
    correlation_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    status TEXT NOT NULL,
    items JSONB NOT NULL,
    payment JSONB NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL,
    transaction_id TEXT NULL,
    invoice_id UUID NULL,
    invoice_status TEXT NULL,
    warehouse_commit_status TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shop_orders_correlation_id
    ON shop_orders (correlation_id);
"""


def init_database() -> None:
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_ORDERS_TABLE_SQL)
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS transaction_id TEXT NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS invoice_id UUID NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS invoice_status TEXT NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS warehouse_commit_status TEXT NULL;")


def create_order(
    order_id: str,
    correlation_id: str,
    customer_id: str,
    items: list[dict[str, Any]],
    payment: dict[str, Any],
    amount: str,
    currency: str,
) -> None:
    query = """
    INSERT INTO shop_orders (
        id,
        correlation_id,
        customer_id,
        status,
        items,
        payment,
        amount,
        currency
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    order_id,
                    correlation_id,
                    customer_id,
                    "PENDING",
                    Jsonb(items),
                    Jsonb(payment),
                    amount,
                    currency,
                ),
            )


def update_order_status(order_id: str, status: str) -> None:
    query = """
    UPDATE shop_orders
    SET status = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (status, order_id))


def update_payment_succeeded(order_id: str, transaction_id: str) -> None:
    query = """
    UPDATE shop_orders
    SET status = %s, transaction_id = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, ("PAYMENT_SUCCEEDED", transaction_id, order_id))


def update_invoice_created(order_id: str, invoice_id: str) -> None:
    query = """
    UPDATE shop_orders
    SET invoice_id = %s, invoice_status = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (invoice_id, "CREATED", order_id))


def update_warehouse_commit(order_id: str, commit_status: str) -> None:
    query = """
    UPDATE shop_orders
    SET warehouse_commit_status = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (commit_status, order_id))


def complete_order_if_ready(order_id: str) -> bool:
    query = """
    UPDATE shop_orders
    SET status = %s, updated_at = now()
    WHERE id = %s
      AND transaction_id IS NOT NULL
      AND invoice_status = %s
      AND warehouse_commit_status = %s
      AND status <> %s
    RETURNING id;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, ("COMPLETED", order_id, "CREATED", "SUCCEEDED", "COMPLETED"))
            return cursor.fetchone() is not None


def get_order(order_id: str) -> dict[str, Any] | None:
    query = """
    SELECT
        id AS "orderId",
        correlation_id AS "correlationId",
        status,
        amount,
        currency,
        transaction_id AS "transactionId",
        invoice_id AS "invoiceId",
        invoice_status AS "invoiceStatus",
        warehouse_commit_status AS "warehouseCommitStatus"
    FROM shop_orders
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (order_id,))
            return cursor.fetchone()


def get_orders_by_correlation_id(correlation_id: str) -> Iterable[dict[str, Any]]:
    query = """
    SELECT
        id AS "orderId",
        correlation_id AS "correlationId",
        status,
        amount,
        currency,
        transaction_id AS "transactionId",
        invoice_id AS "invoiceId",
        invoice_status AS "invoiceStatus",
        warehouse_commit_status AS "warehouseCommitStatus"
    FROM shop_orders
    WHERE correlation_id = %s
    ORDER BY created_at ASC;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (correlation_id,))
            return cursor.fetchall()
