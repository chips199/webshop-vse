from decimal import Decimal

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
    return sum((Decimal(item["lineTotal"]) for item in enrich_items(items)), Decimal("0.00"))
