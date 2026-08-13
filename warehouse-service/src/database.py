"""Datenbankzugriff des warehouse-service.

Zwei Tabellen: warehouse_stock (aktueller Bestand je Produkt, inkl. bereits
reservierter Menge) und warehouse_reservations (eine Zeile je Bestellung mit
Reservierungs-Status RESERVED/COMMITTED/CANCELLED). Alle bestandsaendernden
Funktionen (reserve_stock/commit_reservation/cancel_reservation) verwenden
"SELECT ... FOR UPDATE" innerhalb einer Transaktion, um Race Conditions bei
gleichzeitigen Bestellungen auf denselben Artikel zu verhindern (siehe
Kommentare dort).
"""

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

# Anfangsbestand fuer die Demo-/Testprodukte des Shops (siehe shop-service).
# Feste Produkt-UUIDs statt generierter IDs, damit sie im Frontend/den
# Testskripten stabil referenziert werden koennen. Wird nur beim allerersten
# Start eingespielt (init_database() nutzt ON CONFLICT DO NOTHING).
STOCK_SEED: dict[str, dict[str, Any]] = {
    "22222222-2222-2222-2222-222222222222": {"quantity": 7, "location": "CPU-A1"},
    "33333333-3333-3333-3333-333333333333": {"quantity": 4, "location": "IC-B2"},
    "44444444-4444-4444-4444-444444444444": {"quantity": 3, "location": "KEY-C1"},
    "55555555-5555-5555-5555-555555555555": {"quantity": 2, "location": "SYS-D4"},
    "66666666-6666-6666-6666-666666666666": {"quantity": 5, "location": "CPU-A2"},
    "77777777-7777-7777-7777-777777777777": {"quantity": 8, "location": "CPU-A3"},
    "88888888-8888-8888-8888-888888888888": {"quantity": 6, "location": "CPU-A4"},
    "99999999-9999-9999-9999-999999999999": {"quantity": 4, "location": "CPU-A5"},
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {"quantity": 6, "location": "DRV-B1"},
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": {"quantity": 3, "location": "DRV-B2"},
    "cccccccc-cccc-cccc-cccc-cccccccccccc": {"quantity": 4, "location": "CARD-C1"},
    "dddddddd-dddd-dddd-dddd-dddddddddddd": {"quantity": 2, "location": "CARD-C2"},
    "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee": {"quantity": 5, "location": "CARD-C3"},
    "ffffffff-ffff-ffff-ffff-ffffffffffff": {"quantity": 2, "location": "CARD-C4"},
    "12121212-1212-1212-1212-121212121212": {"quantity": 12, "location": "RAM-D1"},
    "13131313-1313-1313-1313-131313131313": {"quantity": 10, "location": "RAM-D2"},
    "14141414-1414-1414-1414-141414141414": {"quantity": 3, "location": "DRV-B3"},
    "15151515-1515-1515-1515-151515151515": {"quantity": 3, "location": "CARD-C5"},
    "16161616-1616-1616-1616-161616161616": {"quantity": 2, "location": "SYS-D5"},
    "17171717-1717-1717-1717-171717171717": {"quantity": 7, "location": "ACC-E1"},
    "18181818-1818-1818-1818-181818181818": {"quantity": 9, "location": "ACC-E2"},
    "19191919-1919-1919-1919-191919191919": {"quantity": 3, "location": "PSU-F1"},
    "20202020-2020-2020-2020-202020202020": {"quantity": 5, "location": "GAME-G1"},
    "21212121-2121-2121-2121-212121212121": {"quantity": 4, "location": "NET-H1"},
}


def init_database() -> None:
    """Legt Tabellen an und spielt den Anfangsbestand (STOCK_SEED) ein.

    Idempotent: CREATE TABLE IF NOT EXISTS und ON CONFLICT DO NOTHING sorgen
    dafuer, dass ein Neustart des Service den bereits veraenderten Bestand
    nicht ueberschreibt.
    """
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
    """Liefert den aktuellen Bestand aller Produkte (fuer GET /stock)."""
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
    """Legt einen Lagerbestand fuer ein neues Produkt an (Admin-Funktion).

    "ON CONFLICT ... DO UPDATE" statt striktem INSERT: erlaubt es, den
    Aufruf gefahrlos zu wiederholen (z.B. nach einem Netzwerkfehler beim
    ersten Versuch), ohne vorher pruefen zu muessen, ob der Datensatz schon
    existiert.
    """
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
    """Setzt den Gesamtbestand eines Produkts neu (Admin-Funktion).

    "FOR UPDATE" sperrt die Zeile fuer die Dauer der Transaktion, damit
    zwischen dem Lesen von reserved_quantity und der Validierung darunter
    keine gleichzeitige Reservierung/Buchung dazwischenfunken kann (sonst
    koennte quantity_on_hand am Ende faelschlich unter die tatsaechlich
    reservierte Menge fallen).
    """
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
                    # Der neue Gesamtbestand darf nicht kleiner sein als das,
                    # was bereits fuer laufende Bestellungen reserviert ist -
                    # sonst waere availableQuantity (= on_hand - reserved)
                    # negativ.
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
    """Prueft Verfuegbarkeit und reserviert Artikel fuer eine Bestellung.

    Rueckgabe (True, None) bei Erfolg, sonst (False, reasonCode).
    Ablauf innerhalb einer einzigen DB-Transaktion:
      1. Idempotenz-Check: wurde fuer order_id schon einmal reserviert
         (z.B. weil warehouse.reserve.requested doppelt zugestellt wurde),
         wird nicht erneut reserviert, sondern nur der bisherige Ausgang
         zurueckgemeldet.
      2. Fuer JEDEN Artikel zuerst pruefen (mit FOR UPDATE gesperrt), dann
         erst reservieren (zweite Schleife unten) - erst wenn ALLE Artikel
         verfuegbar sind, wird ueberhaupt etwas geschrieben. Sonst koennte
         bei einer Bestellung mit mehreren Artikeln der erste Artikel schon
         reserviert sein, obwohl der zweite nicht mehr verfuegbar ist.
      3. FOR UPDATE sperrt die betroffenen Bestandszeilen bis Transaktionsende
         - das ist die eigentliche Absicherung gegen die Race Condition
         "zwei Bestellungen reservieren gleichzeitig den letzten Artikel".
    """
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

                # Erst hier, nachdem ALLE Artikel als verfuegbar bestaetigt
                # sind, wird tatsaechlich reserviert (reserved_quantity
                # erhoehen + Reservierungs-Datensatz anlegen).
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
    """Bucht eine reservierte Bestellung final aus dem Lager aus (Schritt 6
    im Happy Path, nach erfolgreicher Zahlung: warehouse.commit.requested).

    quantity_on_hand UND reserved_quantity werden gemeinsam reduziert - die
    Ware ist damit sowohl physisch weg als auch nicht mehr "reserviert"
    (die Reservierung ist ja jetzt final geworden). Wenn schon COMMITTED:
    True zurueckgeben statt eines Fehlers - macht den Aufruf idempotent bei
    doppelt zugestellten Nachrichten.
    """
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
    """Storniert eine Reservierung (Saga-Kompensation bei abgelehnter Zahlung
    oder gezieltem out_of_stock-Testszenario, siehe main.py).

    Anders als commit_reservation() wird hier NUR reserved_quantity
    reduziert, quantity_on_hand bleibt unveraendert - die Ware war ja nie
    wirklich weg, nur fuer diese Bestellung vorgemerkt. Reservierung nicht
    gefunden -> True (nichts zu stornieren, kein Fehlerzustand): passiert
    z.B. im out_of_stock-Testszenario, wo reserve_stock() vorher schon
    fehlgeschlagen ist und real gar keine Reservierung existiert.
    """
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
    """Liest eine Reservierung mit Zeilensperre (FOR UPDATE) - gemeinsam
    genutzt von commit_reservation() und cancel_reservation(), damit nicht
    gleichzeitig committed UND cancelled werden kann."""
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
