"""Datenbankzugriff des shop-service.

Groesster Service, entsprechend die meiste Datenbanklogik: Bestellungen
(shop_orders - der "Saga-Zustand" aus Sicht von shop-service), der
Produktkatalog (products, inkl. Seed-Daten) sowie Admin-Authentifizierung
(admin_users/admin_sessions). Reine SQL-Wrapper-Funktionen ohne Business-
Logik - die Saga-Entscheidungen (wann welches Event publiziert wird)
liegen in main.py.
"""

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

# Feste Produktdaten (Name, Preis, Bild-Metadaten) fuer den Katalog des
# Webshops - historische Computerteile mit fixen UUIDs, damit sie mit
# catalog.py und den Bestand-Seed-Daten in warehouse-service uebereinstimmen.
# Wird bei jedem Start ueber _seed_products() erneut eingespielt (siehe dort).
PRODUCT_SEED: list[dict[str, str]] = [
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Intel 8086 CPU",
        "year": "1978",
        "description": "16-Bit-Mikroprozessor im DIP-40-Gehaeuse, Grundstein der x86-Familie.",
        "price": "149.90",
        "currency": "EUR",
        "imageUrl": "/product-images/intel-8086-cpu.png",
        "imageAlt": "KI-generiertes Pixelbild eines Intel 8086 Prozessors",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "Commodore 64 SID 6581",
        "year": "1982",
        "description": "Originaler Sound Interface Device Chip fuer warme Filter und knisternde Chiptunes.",
        "price": "89.90",
        "currency": "EUR",
        "imageUrl": "/product-images/commodore-64-sid-6581.png",
        "imageAlt": "KI-generiertes Pixelbild eines Commodore SID 6581 Chips",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "44444444-4444-4444-4444-444444444444",
        "name": "IBM Model M Keyboard",
        "year": "1985",
        "description": "Buckling-Spring-Tastatur mit schwerem Gehaeuse und klassischem Schreibgefuehl.",
        "price": "129.00",
        "currency": "EUR",
        "imageUrl": "/product-images/ibm-model-m-keyboard.png",
        "imageAlt": "KI-generiertes Pixelbild einer IBM Model M Tastatur",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "55555555-5555-5555-5555-555555555555",
        "name": "Commodore Amiga 500 System",
        "year": "1987",
        "description": "Klassischer Heimcomputer mit Motorola-68000-Architektur, Tastatur und Diskettenlaufwerk.",
        "price": "349.00",
        "currency": "EUR",
        "imageUrl": "/product-images/commodore-amiga-500-system.png",
        "imageAlt": "KI-generiertes Pixelbild eines Commodore Amiga 500 Systems",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "66666666-6666-6666-6666-666666666666",
        "name": "Motorola 68000 CPU",
        "year": "1979",
        "description": "16/32-Bit-Prozessor, bekannt aus Amiga, Atari ST und fruehen Workstations.",
        "price": "119.90",
        "currency": "EUR",
        "imageUrl": "/product-images/motorola-68000-cpu.png",
        "imageAlt": "KI-generiertes Pixelbild einer Motorola 68000 CPU",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "77777777-7777-7777-7777-777777777777",
        "name": "Zilog Z80 CPU",
        "year": "1976",
        "description": "8-Bit-Klassiker fuer CP/M-Systeme, Heimcomputer und Arcade-Hardware.",
        "price": "39.90",
        "currency": "EUR",
        "imageUrl": "/product-images/zilog-z80-cpu.png",
        "imageAlt": "KI-generiertes Pixelbild einer Zilog Z80 CPU",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "88888888-8888-8888-8888-888888888888",
        "name": "MOS 6502 CPU",
        "year": "1975",
        "description": "Legendärer 8-Bit-Prozessor aus Apple II, C64-Verwandtschaft und NES-Aera.",
        "price": "54.90",
        "currency": "EUR",
        "imageUrl": "/product-images/mos-6502-cpu.png",
        "imageAlt": "KI-generiertes Pixelbild einer MOS 6502 CPU",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "99999999-9999-9999-9999-999999999999",
        "name": "Intel 486 DX2 CPU",
        "year": "1992",
        "description": "Taktverdoppelter 486-Prozessor fuer schnelle DOS-Spiele und fruehe 90er-PCs.",
        "price": "79.90",
        "currency": "EUR",
        "imageUrl": "/product-images/intel-486-dx2-cpu.png",
        "imageAlt": "KI-generiertes Pixelbild einer Intel 486 DX2 CPU",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "name": "3.5 Zoll Floppy-Laufwerk",
        "year": "1984",
        "description": "Internes 3.5-Zoll-Diskettenlaufwerk fuer klassische PC- und Amiga-Setups.",
        "price": "34.90",
        "currency": "EUR",
        "imageUrl": "/product-images/floppy-drive-35.png",
        "imageAlt": "KI-generiertes Pixelbild eines 3.5 Zoll Floppy-Laufwerks",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "name": "5.25 Zoll Floppy-Laufwerk",
        "year": "1981",
        "description": "Klassisches 5.25-Zoll-Laufwerk fuer XT-, AT- und fruehe DOS-Systeme.",
        "price": "49.90",
        "currency": "EUR",
        "imageUrl": "/product-images/floppy-drive-525.png",
        "imageAlt": "KI-generiertes Pixelbild eines 5.25 Zoll Floppy-Laufwerks",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "name": "Sound Blaster 16 ISA",
        "year": "1992",
        "description": "ISA-Soundkarte fuer DOS-Spiele mit FM-Synthese und 16-Bit-Audio.",
        "price": "99.90",
        "currency": "EUR",
        "imageUrl": "/product-images/sound-blaster-16-isa.png",
        "imageAlt": "KI-generiertes Pixelbild einer Sound Blaster 16 ISA Karte",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "name": "3dfx Voodoo2 PCI",
        "year": "1998",
        "description": "Legendäre 3D-Beschleunigerkarte fuer Glide-Spiele und Retro-Gaming-PCs.",
        "price": "249.00",
        "currency": "EUR",
        "imageUrl": "/product-images/3dfx-voodoo2-pci.png",
        "imageAlt": "KI-generiertes Pixelbild einer 3dfx Voodoo2 PCI Karte",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "name": "S3 Trio64V+ PCI",
        "year": "1995",
        "description": "Solide 2D-Grafikkarte fuer Windows 95, DOS und VGA-Ausgabe.",
        "price": "59.90",
        "currency": "EUR",
        "imageUrl": "/product-images/s3-trio64v-pci.png",
        "imageAlt": "KI-generiertes Pixelbild einer S3 Trio64V+ PCI Grafikkarte",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "name": "AdLib Soundkarte",
        "year": "1987",
        "description": "OPL2-Soundkarte fuer authentische FM-Musik in fruehen PC-Spielen.",
        "price": "159.00",
        "currency": "EUR",
        "imageUrl": "/product-images/adlib-sound-card.png",
        "imageAlt": "KI-generiertes Pixelbild einer AdLib Soundkarte",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "12121212-1212-1212-1212-121212121212",
        "name": "72-Pin SIMM RAM Modul",
        "year": "1993",
        "description": "Speichermodul fuer 486- und fruehe Pentium-Systeme.",
        "price": "24.90",
        "currency": "EUR",
        "imageUrl": "/product-images/simm-72-pin.png",
        "imageAlt": "KI-generiertes Pixelbild eines 72-Pin SIMM RAM Moduls",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "13131313-1313-1313-1313-131313131313",
        "name": "30-Pin SIMM RAM Modul",
        "year": "1988",
        "description": "Kompaktes Speichermodul fuer 286-, 386- und fruehe Macintosh-Systeme.",
        "price": "19.90",
        "currency": "EUR",
        "imageUrl": "/product-images/simm-30-pin.png",
        "imageAlt": "KI-generiertes Pixelbild eines 30-Pin SIMM RAM Moduls",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "14141414-1414-1414-1414-141414141414",
        "name": "IDE Festplatte 420 MB",
        "year": "1994",
        "description": "Mechanische IDE-Festplatte mit passender Kapazitaet fuer DOS- und Windows-95-Rechner.",
        "price": "69.90",
        "currency": "EUR",
        "imageUrl": "/product-images/ide-hard-drive-420mb.png",
        "imageAlt": "KI-generiertes Pixelbild einer IDE Festplatte",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "15151515-1515-1515-1515-151515151515",
        "name": "SCSI Controller Karte",
        "year": "1991",
        "description": "Controllerkarte fuer Scanner, Festplatten und externe SCSI-Geraete.",
        "price": "84.90",
        "currency": "EUR",
        "imageUrl": "/product-images/scsi-controller-card.png",
        "imageAlt": "KI-generiertes Pixelbild einer SCSI Controller Karte",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "16161616-1616-1616-1616-161616161616",
        "name": "Beiger CRT Monitor",
        "year": "1990",
        "description": "Kompakter Roehrenmonitor fuer VGA-Setups mit echtem Retro-Look.",
        "price": "189.00",
        "currency": "EUR",
        "imageUrl": "/product-images/beige-crt-monitor.png",
        "imageAlt": "KI-generiertes Pixelbild eines beigen CRT Monitors",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "17171717-1717-1717-1717-171717171717",
        "name": "Serielle Maus",
        "year": "1986",
        "description": "Kabelmaus mit serieller Schnittstelle fuer DOS- und Windows-3.x-Rechner.",
        "price": "29.90",
        "currency": "EUR",
        "imageUrl": "/product-images/serial-mouse.png",
        "imageAlt": "KI-generiertes Pixelbild einer seriellen Maus",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "18181818-1818-1818-1818-181818181818",
        "name": "Parallelport-Druckerkabel",
        "year": "1989",
        "description": "Robustes DB25-Druckerkabel fuer Nadeldrucker und alte PC-Systeme.",
        "price": "14.90",
        "currency": "EUR",
        "imageUrl": "/product-images/parallel-printer-cable.png",
        "imageAlt": "KI-generiertes Pixelbild eines Parallelport-Druckerkabels",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "19191919-1919-1919-1919-191919191919",
        "name": "AT Netzteil",
        "year": "1990",
        "description": "Klassisches AT-Netzteil fuer 286-, 386- und 486-Gehaeuse.",
        "price": "74.90",
        "currency": "EUR",
        "imageUrl": "/product-images/at-power-supply.png",
        "imageAlt": "KI-generiertes Pixelbild eines AT Netzteils",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "20202020-2020-2020-2020-202020202020",
        "name": "Retro Joystick",
        "year": "1983",
        "description": "Schwarzer Joystick mit rotem Feuerknopf fuer Heimcomputer und Arcade-Gefuehl.",
        "price": "44.90",
        "currency": "EUR",
        "imageUrl": "/product-images/retro-joystick.png",
        "imageAlt": "KI-generiertes Pixelbild eines Retro Joysticks",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
    {
        "id": "21212121-2121-2121-2121-212121212121",
        "name": "Externes 56k Modem",
        "year": "1997",
        "description": "Externes Dial-up-Modem mit Status-LEDs fuer authentische Online-Nostalgie.",
        "price": "39.90",
        "currency": "EUR",
        "imageUrl": "/product-images/external-modem.png",
        "imageAlt": "KI-generiertes Pixelbild eines externen 56k Modems",
        "imageSource": "OpenAI image generation",
        "imageLicense": "AI generated project asset",
        "imageCredit": "OpenAI",
    },
]


def init_database() -> None:
    """Legt alle Tabellen/Indizes an (idempotent) und fuehrt die noetigen
    ALTER-TABLE-Migrationen fuer spaeter hinzugekommene Spalten aus, bevor
    Produkte und der Admin-User geseedet werden. Wird beim Start von main.py
    (lifespan()) aufgerufen."""
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
            cursor.execute("ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS payment_redirect_url TEXT NULL;")
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
    """Legt eine neue Bestellung mit Status PENDING an (Start der Saga).

    idempotency_key/request_hash (Bonusaufgabe 4.2) erlauben es main.py,
    einen wiederholten POST /orders mit demselben Idempotency-Key als
    dieselbe Bestellung zu erkennen, statt eine zweite anzulegen.
    """
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
    """Setzt den Saga-Status einer Bestellung direkt (fuer alle Zwischen-/
    Endzustaende, die keine weiteren Felder mitbringen, z.B. CANCELLED,
    ORDER_REJECTED, INVOICE_FAILED)."""
    query = """
    UPDATE shop_orders
    SET status = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (status, order_id))


def update_payment_action_required(order_id: str, transaction_id: str, redirect_url: str) -> None:
    """Markiert die Bestellung als wartend auf externe Zahlungsbestaetigung
    (z.B. PayPal/Stripe-Redirect) und speichert die dafuer noetige
    transaction_id sowie die Redirect-URL fuer das Frontend."""
    query = """
    UPDATE shop_orders
    SET status = %s, transaction_id = %s, payment_redirect_url = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, ("PAYMENT_ACTION_REQUIRED", transaction_id, redirect_url, order_id))


def update_payment_succeeded(
    order_id: str,
    transaction_id: str,
    customer: dict[str, Any] | None = None,
    shipping_address: dict[str, Any] | None = None,
) -> None:
    """Markiert die Bestellung als bezahlt und speichert die transaction_id.

    customer/shipping_address kommen nur befuellt an, wenn der Kaeufer sie
    auf der echten Stripe-/PayPal-Sandbox-Seite eingegeben hat (siehe
    billing-service PaymentResult.customer/shipping_address). COALESCE
    sorgt dafuer, dass ohne solche Daten (z.B. lokaler Stub-Modus ohne
    Sandbox-Credentials) die im Checkout-Formular erfassten Werte stehen
    bleiben, statt mit NULL ueberschrieben zu werden.
    """
    query = """
    UPDATE shop_orders
    SET status = %s,
        transaction_id = %s,
        customer = COALESCE(%s, customer),
        shipping_address = COALESCE(%s, shipping_address),
        updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    "PAYMENT_SUCCEEDED",
                    transaction_id,
                    Jsonb(customer) if customer else None,
                    Jsonb(shipping_address) if shipping_address else None,
                    order_id,
                ),
            )


def update_invoice_created(order_id: str, invoice_id: str) -> None:
    """Verknuepft die Bestellung mit der erzeugten Rechnung (invoice.created)."""
    query = """
    UPDATE shop_orders
    SET invoice_id = %s, invoice_status = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (invoice_id, "CREATED", order_id))


def update_warehouse_commit(order_id: str, commit_status: str) -> None:
    """Speichert das Ergebnis des finalen Lager-Commits (warehouse.commit.*)."""
    query = """
    UPDATE shop_orders
    SET warehouse_commit_status = %s, updated_at = now()
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (commit_status, order_id))


def claim_payment_confirmation(order_id: str) -> bool:
    """Beansprucht die einmalige Zahlungsbestaetigung einer Order atomar.

    Der Endpunkt POST /orders/{orderId}/payment-confirmation las den Status
    zuvor per SELECT und verliess sich auf einen anschliessenden Vergleich in
    Python - zwischen SELECT und dem Publizieren von
    billing.payment.confirm.requested lag ein Zeitfenster, in dem ein
    zweiter (paralleler) Aufruf denselben PAYMENT_ACTION_REQUIRED-Status noch
    sehen und ebenfalls das Event ausloesen konnte. Bei PayPal fuehrt das im
    Sandbox-Modus zu einem doppelten capture_order()-Aufruf.

    Das WHERE status = 'PAYMENT_ACTION_REQUIRED' macht die Status-Pruefung
    und den Uebergang atomar (Postgres serialisiert konkurrierende UPDATEs
    auf derselben Zeile): nur der zuerst ankommende Aufruf trifft und darf
    das Confirm-Event publizieren, jeder weitere erhaelt False und damit
    einen 409-Conflict, statt einen zweiten Confirm-Request auszuloesen.
    Ersetzt keine vollstaendige Idempotenzloesung mit Idempotency-Key
    (Bonusaufgabe 4.2), schliesst aber genau diese Race Condition.
    """
    query = """
    UPDATE shop_orders
    SET status = %s, updated_at = now()
    WHERE id = %s
      AND status = %s
    RETURNING id;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                ("PAYMENT_CONFIRMATION_PENDING", order_id, "PAYMENT_ACTION_REQUIRED"),
            )
            return cursor.fetchone() is not None


def complete_order_if_ready(order_id: str) -> bool:
    """Schliesst die Bestellung ab (COMPLETED), aber NUR wenn alle drei
    Saga-Zweige erfolgreich durch sind: Zahlung bestaetigt (transaction_id
    gesetzt), Rechnung erzeugt (invoice_status = CREATED) und Lagerbestand
    final committed (warehouse_commit_status = SUCCEEDED). Da diese drei
    Events asynchron und in beliebiger Reihenfolge eintreffen koennen, wird
    diese Funktion nach JEDEM der drei Teilschritte erneut aufgerufen (siehe
    main.py) - die WHERE-Bedingung macht das Ergebnis unabhaengig von der
    Aufrufreihenfolge und verhindert per `status <> 'COMPLETED'` einen
    doppelten Abschluss."""
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
    """Liest eine einzelne Bestellung mit allen Feldern (fuer GET /orders/{id})."""
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
        payment,
        items,
        transaction_id AS "transactionId",
        invoice_id AS "invoiceId",
        invoice_status AS "invoiceStatus",
        warehouse_commit_status AS "warehouseCommitStatus",
        payment_redirect_url AS "paymentRedirectUrl"
    FROM shop_orders
    WHERE id = %s;
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (order_id,))
            return cursor.fetchone()


def get_order_by_idempotency_key(idempotency_key: str) -> dict[str, Any] | None:
    """Sucht eine bereits angelegte Bestellung anhand ihres Idempotency-Keys
    (Bonusaufgabe 4.2) - main.py nutzt das, um einen wiederholten POST
    /orders mit demselben Key als Duplikat zu erkennen statt eine zweite
    Bestellung anzulegen."""
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
    """Liest alle Bestellungen zu einer correlationId (i.d.R. genau eine -
    wird u.a. fuer die Audit-Timeline im Admin-Dashboard genutzt)."""
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
    """Liefert alle aktiven Produkte fuer den Katalog (GET /products), sortiert
    nach Baujahr und Name."""
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
    """Legt ein neues Produkt an (Admin-Dashboard: Produktverwaltung)."""
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
    """Aktualisiert ein bestehendes, aktives Produkt; gibt None zurueck, falls
    die ID nicht existiert oder das Produkt bereits deaktiviert wurde."""
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
    """Berechnet Name/Stueckpreis/Zeilensumme je Bestellposition serverseitig
    aus der products-Tabelle (NIE aus Client-Angaben - verhindert
    Preismanipulation, siehe Aufgabenblatt 5.2). Wirft ValueError, falls eine
    productId nicht (mehr) im aktiven Katalog existiert."""
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
    """Summiert die (bereits serverseitig berechneten) Zeilensummen zum
    Gesamtbetrag der Bestellung."""
    return sum((Decimal(item["lineTotal"]) for item in items), Decimal("0.00"))


def list_admin_orders(limit: int = 50) -> list[dict[str, Any]]:
    """Liefert die neuesten Bestellungen fuer die Admin-Dashboard-Uebersicht
    (Zeitraum-Filterung passiert im Frontend/main.py, nicht hier)."""
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
    """Liest die Audit-Timeline direkt per SQL aus der audit_snapshots-Tabelle.

    HINWEIS: main.py nutzt fuer die Admin-Timeline stattdessen
    fetch_audit_snapshots() (HTTP-Aufruf an audit-service, siehe dort) statt
    dieser Funktion, um den "kein Service liest fremde Tabellen"-Grundsatz
    einzuhalten - diese Funktion bleibt als Altlast/Alternative bestehen."""
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
    """Prueft Admin-Login-Daten gegen den gespeicherten PBKDF2-Hash.

    hmac.compare_digest() statt "==" fuer den Hash-Vergleich verhindert
    Timing-Angriffe (konstante Vergleichszeit unabhaengig davon, an welcher
    Stelle die Hashes voneinander abweichen).
    """
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
    """Speichert eine neue Admin-Session anhand ihres Token-HASHES (nicht des
    Klartext-Tokens - selbst bei DB-Zugriff laesst sich damit kein gueltiges
    Session-Cookie faelschen)."""
    query = """
    INSERT INTO admin_sessions (token_hash, username, expires_at)
    VALUES (%s, %s, %s);
    """
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (token_hash, username, expires_at))


def get_admin_session(token_hash: str) -> dict[str, Any] | None:
    """Liest eine Admin-Session anhand ihres Token-Hashes, aber NUR wenn sie
    noch nicht abgelaufen ist (expires_at > now() direkt in der WHERE-Klausel,
    kein separater Ablauf-Check in Python noetig)."""
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
    """Loescht eine Admin-Session (Logout)."""
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM admin_sessions WHERE token_hash = %s;", (token_hash,))


def _get_products_by_ids(product_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Helfer fuer enrich_items_from_products(): liest mehrere Produkte in
    einer Abfrage (ANY(%s::uuid[])) und liefert sie als Dict productId ->
    Produkt, damit sie pro Bestellposition ohne weitere DB-Roundtrips
    nachgeschlagen werden koennen."""
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
    """Spielt PRODUCT_SEED bei jedem Start ein - ON CONFLICT DO UPDATE
    aktualisiert nur die Bild-Metadaten und setzt active=TRUE wieder, laesst
    Preis/Name/Beschreibung eines bereits bestehenden Eintrags aber
    unangetastet (damit Admin-Aenderungen an bestehenden Produkten einen
    Neustart ueberleben)."""
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
    ON CONFLICT (id) DO UPDATE SET
        image_url = EXCLUDED.image_url,
        image_alt = EXCLUDED.image_alt,
        image_source = EXCLUDED.image_source,
        image_license = EXCLUDED.image_license,
        image_credit = EXCLUDED.image_credit,
        active = TRUE,
        updated_at = now();
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
    """Legt den konfigurierten Admin-Account einmalig an (nicht ON CONFLICT-
    basiert wie bei den Produkten, sondern expliziter Existenz-Check davor -
    verhindert, dass ein bereits per Admin-Dashboard geaendertes Passwort bei
    jedem Neustart wieder auf admin_password aus der Konfiguration
    zurueckgesetzt wird)."""
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
    """Leitet einen PBKDF2-HMAC-SHA256-Hash mit 210.000 Iterationen aus
    Passwort+Salt ab (OWASP-Empfehlung fuer PBKDF2-SHA256, Stand der
    Erstellung) - deutlich sicherer als ein reines SHA256(password)."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        210_000,
    ).hex()
