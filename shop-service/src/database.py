from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
import hashlib
import hmac
import os
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
    customer JSONB NULL,
    shipping_address JSONB NULL,
    billing_address JSONB NULL,
    status TEXT NOT NULL,
    items JSONB NOT NULL,
    payment JSONB NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL,
    transaction_id TEXT NULL,
    invoice_id UUID NULL,
    invoice_status TEXT NULL,
    warehouse_commit_status TEXT NULL,
    idempotency_key TEXT NULL,
    request_hash TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shop_orders_correlation_id
    ON shop_orders (correlation_id);
"""

CREATE_PRODUCTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    year TEXT NOT NULL,
    description TEXT NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    image_url TEXT NOT NULL,
    image_alt TEXT NOT NULL,
    image_source TEXT NOT NULL,
    image_license TEXT NOT NULL,
    image_credit TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_ADMIN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS admin_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES admin_users(username) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires_at
    ON admin_sessions (expires_at);
"""

PRODUCT_SEED: list[dict[str, str]] = [
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Intel 8086 CPU",
        "year": "1978",
        "description": "16-Bit-Mikroprozessor im DIP-40-Gehaeuse, Grundstein der x86-Familie.",
        "price": "149.90",
        "currency": "EUR",
        "imageUrl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/Intel_C8086.jpg/640px-Intel_C8086.jpg",
        "imageAlt": "Intel C8086 Prozessor im Keramikgehaeuse",
        "imageSource": "https://en.wikipedia.org/wiki/Intel_8086",
        "imageLicense": "Wikimedia Commons",
        "imageCredit": "Wikimedia Commons contributors",
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "Commodore 64 SID 6581",
        "year": "1982",
        "description": "Originaler Sound Interface Device Chip fuer warme Filter und knisternde Chiptunes.",
        "price": "89.90",
        "currency": "EUR",
        "imageUrl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/SID_chips.jpg/640px-SID_chips.jpg",
        "imageAlt": "MOS Technology SID Soundchips",
        "imageSource": "https://en.wikipedia.org/wiki/MOS_Technology_6581",
        "imageLicense": "Wikimedia Commons",
        "imageCredit": "Wikimedia Commons contributors",
    },
    {
        "id": "44444444-4444-4444-4444-444444444444",
        "name": "IBM Model M Keyboard",
        "year": "1985",
        "description": "Buckling-Spring-Tastatur mit schwerem Gehaeuse und klassischem Schreibgefuehl.",
        "price": "129.00",
        "currency": "EUR",
        "imageUrl": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/IBM_Model_M_Spanish_Keyboard.jpg/640px-IBM_Model_M_Spanish_Keyboard.jpg",
        "imageAlt": "IBM Model M Tastatur",
        "imageSource": "https://en.wikipedia.org/wiki/Model_M_keyboard",
        "imageLicense": "Wikimedia Commons",
        "imageCredit": "Wikimedia Commons contributors",
    },
    {
        "id": "55555555-5555-5555-5555-555555555555",
        "name": "Commodore Amiga 500 System",
        "year": "1987",
        "description": "Klassischer Heimcomputer mit Motorola-68000-Architektur, Tastatur und Diskettenlaufwerk.",
        "price": "349.00",
        "currency": "EUR",
        "imageUrl": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Amiga500_system.jpg/640px-Amiga500_system.jpg",
        "imageAlt": "Commodore Amiga 500 System mit Monitor und Maus",
        "imageSource": "https://en.wikipedia.org/wiki/Amiga_500",
        "imageLicense": "Wikimedia Commons",
        "imageCredit": "Wikimedia Commons contributors",
    },
]


def init_database() -> None:
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_ORDERS_TABLE_SQL)
            cursor.execute(CREATE_PRODUCTS_TABLE_SQL)
            cursor.execute(CREATE_ADMIN_TABLES_SQL)
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS transaction_id TEXT NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS invoice_id UUID NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS invoice_status TEXT NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS warehouse_commit_status TEXT NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS customer JSONB NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS shipping_address JSONB NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS billing_address JSONB NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL;")
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS request_hash TEXT NULL;")
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_shop_orders_idempotency_key
                    ON shop_orders (idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                """
            )
            _seed_products(cursor)
            _seed_admin_user(cursor)


def create_order(
    order_id: str,
    correlation_id: str,
    customer_id: str,
    customer: dict[str, Any],
    shipping_address: dict[str, Any],
    billing_address: dict[str, Any] | None,
    items: list[dict[str, Any]],
    payment: dict[str, Any],
    amount: str,
    currency: str,
    idempotency_key: str | None = None,
    request_hash: str | None = None,
) -> None:
    query = """
    INSERT INTO shop_orders (
        id,
        correlation_id,
        customer_id,
        customer,
        shipping_address,
        billing_address,
        status,
        items,
        payment,
        amount,
        currency,
        idempotency_key,
        request_hash
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    order_id,
                    correlation_id,
                    customer_id,
                    Jsonb(customer),
                    Jsonb(shipping_address),
                    Jsonb(billing_address) if billing_address else None,
                    "PENDING",
                    Jsonb(items),
                    Jsonb(payment),
                    amount,
                    currency,
                    idempotency_key,
                    request_hash,
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
        idempotency_key AS "idempotencyKey",
        request_hash AS "requestHash",
        customer,
        shipping_address AS "shippingAddress",
        billing_address AS "billingAddress",
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


def get_order_by_idempotency_key(idempotency_key: str) -> dict[str, Any] | None:
    query = """
    SELECT
        id AS "orderId",
        correlation_id AS "correlationId",
        status,
        amount,
        currency,
        idempotency_key AS "idempotencyKey",
        request_hash AS "requestHash"
    FROM shop_orders
    WHERE idempotency_key = %s;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (idempotency_key,))
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


def get_products() -> list[dict[str, Any]]:
    query = """
    SELECT
        id,
        name,
        year,
        description,
        price,
        currency,
        image_url AS "imageUrl",
        image_alt AS "imageAlt",
        image_source AS "imageSource",
        image_license AS "imageLicense",
        image_credit AS "imageCredit"
    FROM products
    WHERE active = TRUE
    ORDER BY year ASC, name ASC;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall()


def create_product(product_id: str, product: dict[str, Any]) -> dict[str, Any]:
    query = """
    INSERT INTO products (
        id,
        name,
        year,
        description,
        price,
        currency,
        image_url,
        image_alt,
        image_source,
        image_license,
        image_credit,
        active
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
    RETURNING
        id,
        name,
        year,
        description,
        price,
        currency,
        image_url AS "imageUrl",
        image_alt AS "imageAlt",
        image_source AS "imageSource",
        image_license AS "imageLicense",
        image_credit AS "imageCredit";
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    product_id,
                    product["name"],
                    product["year"],
                    product["description"],
                    product["price"],
                    product["currency"],
                    product["imageUrl"],
                    product["imageAlt"],
                    product.get("imageSource") or "",
                    product.get("imageLicense") or "",
                    product.get("imageCredit") or "",
                ),
            )
            return cursor.fetchone()


def update_product(product_id: str, product: dict[str, Any]) -> dict[str, Any] | None:
    query = """
    UPDATE products
    SET
        name = %s,
        year = %s,
        description = %s,
        price = %s,
        currency = %s,
        image_url = %s,
        image_alt = %s,
        image_source = %s,
        image_license = %s,
        image_credit = %s,
        updated_at = now()
    WHERE id = %s
      AND active = TRUE
    RETURNING
        id,
        name,
        year,
        description,
        price,
        currency,
        image_url AS "imageUrl",
        image_alt AS "imageAlt",
        image_source AS "imageSource",
        image_license AS "imageLicense",
        image_credit AS "imageCredit";
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    product["name"],
                    product["year"],
                    product["description"],
                    product["price"],
                    product["currency"],
                    product["imageUrl"],
                    product["imageAlt"],
                    product.get("imageSource") or "",
                    product.get("imageLicense") or "",
                    product.get("imageCredit") or "",
                    product_id,
                ),
            )
            return cursor.fetchone()


def enrich_items_from_products(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    product_ids = [item["productId"] for item in items]
    products = _get_products_by_ids(product_ids)
    enriched = []
    for item in items:
        product = products.get(item["productId"])
        if product is None:
            raise ValueError(f"Unknown product {item['productId']}")
        unit_price = Decimal(product["price"])
        quantity = int(item["quantity"])
        enriched.append(
            {
                "productId": item["productId"],
                "quantity": quantity,
                "name": product["name"],
                "unitPrice": str(unit_price),
                "lineTotal": str(unit_price * quantity),
            }
        )
    return enriched


def calculate_total(items: list[dict[str, Any]]) -> Decimal:
    return sum((Decimal(item["lineTotal"]) for item in items), Decimal("0.00"))


def list_admin_orders(limit: int = 50) -> list[dict[str, Any]]:
    query = """
    SELECT
        id AS "orderId",
        correlation_id AS "correlationId",
        status,
        amount,
        currency,
        customer,
        shipping_address AS "shippingAddress",
        billing_address AS "billingAddress",
        items,
        payment,
        transaction_id AS "transactionId",
        invoice_id AS "invoiceId",
        invoice_status AS "invoiceStatus",
        warehouse_commit_status AS "warehouseCommitStatus",
        created_at AS "createdAt",
        updated_at AS "updatedAt"
    FROM shop_orders
    ORDER BY created_at DESC
    LIMIT %s;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (limit,))
            return cursor.fetchall()


def get_audit_snapshots_for_order(order_id: str) -> list[dict[str, Any]]:
    order = get_order(order_id)
    if order is None:
        return []
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
            cursor.execute(query, (order["correlationId"],))
            return cursor.fetchall()


def verify_admin_credentials(username: str, password: str) -> bool:
    query = """
    SELECT password_hash, password_salt
    FROM admin_users
    WHERE username = %s;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (username,))
            row = cursor.fetchone()
    if row is None:
        return False
    expected_hash = row["password_hash"]
    candidate_hash = _hash_password(password, row["password_salt"])
    return hmac.compare_digest(candidate_hash, expected_hash)


def create_admin_session(token_hash: str, username: str, expires_at: datetime) -> None:
    query = """
    INSERT INTO admin_sessions (token_hash, username, expires_at)
    VALUES (%s, %s, %s);
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (token_hash, username, expires_at))


def get_admin_session(token_hash: str) -> dict[str, Any] | None:
    query = """
    SELECT username, expires_at AS "expiresAt"
    FROM admin_sessions
    WHERE token_hash = %s
      AND expires_at > now();
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (token_hash,))
            return cursor.fetchone()


def delete_admin_session(token_hash: str) -> None:
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM admin_sessions WHERE token_hash = %s;", (token_hash,))


def _get_products_by_ids(product_ids: list[str]) -> dict[str, dict[str, Any]]:
    query = """
    SELECT id, name, price, currency
    FROM products
    WHERE id = ANY(%s::uuid[])
      AND active = TRUE;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (product_ids,))
            rows = cursor.fetchall()
    return {str(row["id"]): row for row in rows}


def _seed_products(cursor) -> None:
    query = """
    INSERT INTO products (
        id,
        name,
        year,
        description,
        price,
        currency,
        image_url,
        image_alt,
        image_source,
        image_license,
        image_credit
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING;
    """
    for product in PRODUCT_SEED:
        cursor.execute(
            query,
            (
                product["id"],
                product["name"],
                product["year"],
                product["description"],
                product["price"],
                product["currency"],
                product["imageUrl"],
                product["imageAlt"],
                product["imageSource"],
                product["imageLicense"],
                product["imageCredit"],
            ),
        )


def _seed_admin_user(cursor) -> None:
    cursor.execute("SELECT 1 FROM admin_users WHERE username = %s;", (settings.admin_username,))
    if cursor.fetchone() is not None:
        return
    salt = os.urandom(16).hex()
    cursor.execute(
        """
        INSERT INTO admin_users (username, password_hash, password_salt)
        VALUES (%s, %s, %s);
        """,
        (settings.admin_username, _hash_password(settings.admin_password, salt), salt),
    )


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        210_000,
    ).hex()
