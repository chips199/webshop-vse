"""Service-Schicht des shop-service fuer die synchronen HTTP-Endpunkte.

Enthaelt die Business-Logik, die von den Router-Funktionen in routes.py
gebraucht wird, aber nicht selbst HTTP-spezifisch ist: Admin-Session-
Handling (Token-Hashing, require_admin-Dependency), Idempotency-Key-
Verarbeitung (Bonusaufgabe 4.2) und Serialisierung von DB-Datensaetzen in
die jeweiligen Response-Schemas. Die Saga-/Consumer-seitige Business-Logik
(RabbitMQ-Events) liegt getrennt in saga.py.
"""

import hashlib
import json

from fastapi import HTTPException, Request, status

from .database import get_admin_session
from .schemas import CreateOrderRequest, OrderResponse

ADMIN_SESSION_COOKIE = "admin_session"


def _token_hash(token: str) -> str:
    """Hasht ein Admin-Session-Token fuer den Datenbankvergleich - siehe
    create_admin_session()/get_admin_session() in database.py: gespeichert
    wird nie das Klartext-Token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_admin(request: Request) -> str:
    """FastAPI-Dependency: prueft das Admin-Session-Cookie und liefert den
    eingeloggten Benutzernamen, sonst 401. In allen /admin/*-Endpunkten per
    `Depends(require_admin)` eingebunden."""
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    session = get_admin_session(_token_hash(token))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    return session["username"]


def _idempotency_key_from_request(request: Request) -> str | None:
    """Liest und validiert den optionalen Idempotency-Key-Header (Bonusaufgabe
    4.2). None, falls der Header fehlt; 400, falls er leer oder zu lang ist."""
    value = request.headers.get("Idempotency-Key")
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Idempotency-Key header must not be empty")
    if len(stripped) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key header must not exceed 128 characters")
    return stripped


def _request_hash(order: CreateOrderRequest) -> str:
    """Bildet einen stabilen Hash ueber den Request-Body (sortierte Keys,
    kompakte Trennzeichen) - dient dazu, bei Wiederverwendung eines
    Idempotency-Keys zu erkennen, ob es sich um denselben oder einen
    fachlich abweichenden Request handelt (siehe create_order() in routes.py)."""
    canonical = json.dumps(order.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _order_response(order: dict) -> OrderResponse:
    """Serialisiert einen DB-Bestelldatensatz zur oeffentlichen OrderResponse."""
    return OrderResponse(
        orderId=str(order["orderId"]),
        correlationId=str(order["correlationId"]),
        status=order["status"],
        amount=str(order["amount"]),
        currency=order["currency"],
        transactionId=order.get("transactionId"),
        paymentRedirectUrl=order.get("paymentRedirectUrl"),
        customer=order.get("customer"),
        shippingAddress=order.get("shippingAddress"),
    )


def _initial_order_response(order: dict) -> OrderResponse:
    """Wie _order_response(), aber fuer den Idempotenz-Kurzschluss in
    create_order() (routes.py): dort liegen nur wenige Felder vor (der
    DB-Datensatz aus get_order_by_idempotency_key() enthaelt nicht alle
    OrderResponse-Felder), daher der bewusst simplere Status "PENDING" statt
    des tatsaechlichen aktuellen Status."""
    return OrderResponse(
        orderId=str(order["orderId"]),
        correlationId=str(order["correlationId"]),
        status="PENDING",
        amount=str(order["amount"]),
        currency=order["currency"],
    )


def _serialize_product(product: dict, stock: dict | None = None) -> dict:
    """Kombiniert Produkt-Stammdaten (aus shop-service-DB) mit Bestandsdaten
    (aus warehouse-service) zu einem ProductResponse-Dict. Ohne Bestandsdaten
    (warehouse-service nicht erreichbar) bleibt stockStatus "UNKNOWN" statt
    faelschlich "verfuegbar" oder "nicht verfuegbar" zu behaupten."""
    serialized = {
        **product,
        "id": str(product["id"]),
        "price": str(product["price"]),
    }
    if stock is None:
        return {**serialized, "stockStatus": "UNKNOWN"}
    available = int(stock["availableQuantity"])
    return {
        **serialized,
        "quantityOnHand": int(stock["quantityOnHand"]),
        "reservedQuantity": int(stock["reservedQuantity"]),
        "availableQuantity": available,
        "location": stock.get("location"),
        "stockStatus": "OUT_OF_STOCK" if available <= 0 else "AVAILABLE",
    }


def _serialize_order(order: dict) -> dict:
    """Wandelt DB-spezifische Typen (UUID-Objekte) der Admin-Bestellsicht in
    Strings um, wie sie AdminOrderResponse erwartet."""
    return {
        **order,
        "orderId": str(order["orderId"]),
        "correlationId": str(order["correlationId"]),
        "amount": str(order["amount"]),
        "invoiceId": str(order["invoiceId"]) if order.get("invoiceId") else None,
    }


def _serialize_snapshot(snapshot: dict) -> dict:
    """Waere das Gegenstueck zu _serialize_order() fuer Audit-Snapshots (UUID/
    Zeitstempel-Konvertierung). HINWEIS: aktuell ungenutzt, da
    admin_order_audit() (routes.py) die bereits fertig serialisierten
    Snapshots per fetch_audit_snapshots() (HTTP-JSON von audit-service)
    bezieht statt diese Funktion auf ein DB-Ergebnis anzuwenden."""
    return {
        **snapshot,
        "id": str(snapshot["id"]),
        "correlationId": str(snapshot["correlationId"]),
        "previousEventId": str(snapshot["previousEventId"]) if snapshot.get("previousEventId") else None,
        "timestamp": snapshot["timestamp"].isoformat(),
    }
