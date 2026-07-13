from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings

CREATE_STOCK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS warehouse_stock (
    product_id UUID PRIMARY KEY,
    quantity_on_hand INTEGER NOT NULL CHECK (quantity_on_hand >= 0),
    reserved_quantity INTEGER NOT NULL DEFAULT 0 CHECK (reserved_quantity >= 0),
    location TEXT NOT NULL DEFAULT 'RETRO-A1',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_RESERVATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS warehouse_reservations (
    order_id UUID PRIMARY KEY,
    status TEXT NOT NULL,
    items JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

STOCK_SEED: dict[str, dict[str, Any]] = {
    "22222222-2222-2222-2222-222222222222": {"quantity": 7, "location": "CPU-A1"},
    "33333333-3333-3333-3333-333333333333": {"quantity": 4, "location": "IC-B2"},
    "44444444-4444-4444-4444-444444444444": {"quantity": 3, "location": "KEY-C1"},
    "55555555-5555-5555-5555-555555555555": {"quantity": 2, "location": "SYS-D4"},
}


def init_database() -> None:
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_STOCK_TABLE_SQL)
            cursor.execute(CREATE_RESERVATIONS_TABLE_SQL)
            for product_id, stock in STOCK_SEED.items():
                cursor.execute(
                    """
                    INSERT INTO warehouse_stock (product_id, quantity_on_hand, reserved_quantity, location)
                    VALUES (%s, %s, 0, %s)
                    ON CONFLICT (product_id) DO NOTHING;
                    """,
                    (product_id, stock["quantity"], stock["location"]),
                )


def list_stock() -> list[dict[str, Any]]:
    query = """
    SELECT
        product_id AS "productId",
        quantity_on_hand AS "quantityOnHand",
        reserved_quantity AS "reservedQuantity",
        quantity_on_hand - reserved_quantity AS "availableQuantity",
        location,
        updated_at AS "updatedAt"
    FROM warehouse_stock
    ORDER BY product_id ASC;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall()


def create_stock(product_id: str, quantity_on_hand: int, location: str | None = None) -> dict[str, Any]:
    query = """
    INSERT INTO warehouse_stock (product_id, quantity_on_hand, reserved_quantity, location)
    VALUES (%s, %s, 0, %s)
    ON CONFLICT (product_id) DO UPDATE SET
        quantity_on_hand = EXCLUDED.quantity_on_hand,
        location = EXCLUDED.location,
        updated_at = now()
    RETURNING
        product_id AS "productId",
        quantity_on_hand AS "quantityOnHand",
        reserved_quantity AS "reservedQuantity",
        quantity_on_hand - reserved_quantity AS "availableQuantity",
        location,
        updated_at AS "updatedAt";
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (product_id, quantity_on_hand, location or "RETRO-A1"))
            return cursor.fetchone()


def update_stock(product_id: str, quantity_on_hand: int, location: str | None = None) -> dict[str, Any] | None:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT reserved_quantity, location
                    FROM warehouse_stock
                    WHERE product_id = %s
                    FOR UPDATE;
                    """,
                    (product_id,),
                )
                current = cursor.fetchone()
                if current is None:
                    return None
                if quantity_on_hand < int(current["reserved_quantity"]):
                    raise ValueError("quantityOnHand must not be lower than reservedQuantity")
                cursor.execute(
                    """
                    UPDATE warehouse_stock
                    SET quantity_on_hand = %s, location = %s, updated_at = now()
                    WHERE product_id = %s
                    RETURNING
                        product_id AS "productId",
                        quantity_on_hand AS "quantityOnHand",
                        reserved_quantity AS "reservedQuantity",
                        quantity_on_hand - reserved_quantity AS "availableQuantity",
                        location,
                        updated_at AS "updatedAt";
                    """,
                    (quantity_on_hand, location or current["location"], product_id),
                )
                return cursor.fetchone()


def reserve_stock(order_id: str, items: list[dict[str, Any]]) -> tuple[bool, str | None]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM warehouse_reservations WHERE order_id = %s;", (order_id,))
                existing = cursor.fetchone()
                if existing:
                    return existing["status"] in {"RESERVED", "COMMITTED"}, None

                for item in items:
                    cursor.execute(
                        """
                        SELECT quantity_on_hand, reserved_quantity
                        FROM warehouse_stock
                        WHERE product_id = %s
                        FOR UPDATE;
                        """,
                        (item["productId"],),
                    )
                    stock = cursor.fetchone()
                    if stock is None:
                        return False, "UNKNOWN_PRODUCT"
                    available = int(stock["quantity_on_hand"]) - int(stock["reserved_quantity"])
                    if available < int(item["quantity"]):
                        return False, "OUT_OF_STOCK"

                for item in items:
                    cursor.execute(
                        """
                        UPDATE warehouse_stock
                        SET reserved_quantity = reserved_quantity + %s, updated_at = now()
                        WHERE product_id = %s;
                        """,
                        (int(item["quantity"]), item["productId"]),
                    )
                cursor.execute(
                    """
                    INSERT INTO warehouse_reservations (order_id, status, items)
                    VALUES (%s, 'RESERVED', %s);
                    """,
                    (order_id, Jsonb(items)),
                )
    return True, None


def commit_reservation(order_id: str) -> bool:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                reservation = _get_reservation_for_update(cursor, order_id)
                if reservation is None:
                    return False
                if reservation["status"] == "COMMITTED":
                    return True
                if reservation["status"] != "RESERVED":
                    return False
                for item in reservation["items"]:
                    cursor.execute(
                        """
                        UPDATE warehouse_stock
                        SET
                            quantity_on_hand = quantity_on_hand - %s,
                            reserved_quantity = reserved_quantity - %s,
                            updated_at = now()
                        WHERE product_id = %s;
                        """,
                        (int(item["quantity"]), int(item["quantity"]), item["productId"]),
                    )
                cursor.execute(
                    """
                    UPDATE warehouse_reservations
                    SET status = 'COMMITTED', updated_at = now()
                    WHERE order_id = %s;
                    """,
                    (order_id,),
                )
    return True


def cancel_reservation(order_id: str) -> bool:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                reservation = _get_reservation_for_update(cursor, order_id)
                if reservation is None:
                    return True
                if reservation["status"] == "CANCELLED":
                    return True
                if reservation["status"] != "RESERVED":
                    return False
                for item in reservation["items"]:
                    cursor.execute(
                        """
                        UPDATE warehouse_stock
                        SET reserved_quantity = reserved_quantity - %s, updated_at = now()
                        WHERE product_id = %s;
                        """,
                        (int(item["quantity"]), item["productId"]),
                    )
                cursor.execute(
                    """
                    UPDATE warehouse_reservations
                    SET status = 'CANCELLED', updated_at = now()
                    WHERE order_id = %s;
                    """,
                    (order_id,),
                )
    return True


def _get_reservation_for_update(cursor, order_id: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT order_id, status, items
        FROM warehouse_reservations
        WHERE order_id = %s
        FOR UPDATE;
        """,
        (order_id,),
    )
    return cursor.fetchone()
