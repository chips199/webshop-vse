"""Statischer Produktkatalog des shop-service.

Bewusst simpel gehalten (kein eigener Produkt-Service): fest hinterlegte
Preise/Namen zu bekannten Produkt-IDs, mit einem Fallback-Eintrag fuer
alle unbekannten IDs. Wird von create_order() genutzt, um aus reinen
productId/quantity-Angaben vollstaendige Bestellpositionen inkl. Preisen
zu berechnen (Preise kommen NIE vom Client, siehe Aufgabenblatt 5.2 -
Server-seitige Preisberechnung verhindert Manipulation durch den Client).
"""

from decimal import Decimal

# productId -> Stammdaten (Name, Stueckpreis). Die UUIDs sind fix, damit sie
# mit den Lagerbestand-Seed-Daten in warehouse-service/database.py
# uebereinstimmen.
CATALOG: dict[str, dict[str, str]] = {
    "22222222-2222-2222-2222-222222222222": {
        "name": "Intel 8086 CPU, 1978",
        "price": "149.90",
    },
    "33333333-3333-3333-3333-333333333333": {
        "name": "Commodore 64 SID 6581 Sound Chip",
        "price": "89.90",
    },
    "44444444-4444-4444-4444-444444444444": {
        "name": "IBM Model M Keyboard, 1985",
        "price": "129.00",
    },
}

DEFAULT_PART = {
    "name": "Unkatalogisiertes historisches Computerteil",
    "price": "49.90",
}


def enrich_items(items: list[dict]) -> list[dict]:
    """Reichert rohe Bestellpositionen (nur productId/quantity) um Name,
    Stueckpreis und Zeilensumme aus dem Katalog an. Unbekannte productIds
    fallen auf DEFAULT_PART zurueck statt die Bestellung abzulehnen."""
    enriched = []
    for item in items:
        product = CATALOG.get(item["productId"], DEFAULT_PART)
        unit_price = Decimal(product["price"])
        quantity = int(item["quantity"])
        enriched.append(
            {
                **item,
                "name": product["name"],
                "unitPrice": str(unit_price),
                "lineTotal": str(unit_price * quantity),
            }
        )
    return enriched


def calculate_total(items: list[dict]) -> Decimal:
    """Summiert die Zeilensummen aller Positionen zum Bestellgesamtbetrag."""
    return sum((Decimal(item["lineTotal"]) for item in enrich_items(items)), Decimal("0.00"))
