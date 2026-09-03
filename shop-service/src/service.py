"""Hilfsfunktionen der HTTP-Schicht des Shop-Service."""

import hashlib
import json

from fastapi import HTTPException, Request, status

from .database import get_admin_session
from .schemas import CreateOrderRequest, OrderResponse

ADMIN_SESSION_COOKIE = "admin_session"


def _token_hash(token: str) -> str:
    """Hasht ein Admin-Session-Token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_admin(request: Request) -> str:
    """Prueft das Admin-Session-Cookie und liefert den Benutzernamen."""
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    session = get_admin_session(_token_hash(token))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    return session["username"]


def _idempotency_key_from_request(request: Request) -> str | None:
    """Liest und validiert den optionalen Idempotency-Key."""
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
    """Bildet einen stabilen Hash des Request-Bodys."""
    canonical = json.dumps(order.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _order_response(order: dict) -> OrderResponse:
    """Serialisiert eine Bestellung fuer die oeffentliche API."""
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
    """Serialisiert die reduzierte Antwort eines Idempotenz-Treffers."""
    return OrderResponse(
        orderId=str(order["orderId"]),
        correlationId=str(order["correlationId"]),
        status="PENDING",
        amount=str(order["amount"]),
        currency=order["currency"],
    )


def _serialize_product(product: dict, stock: dict | None = None) -> dict:
    """Kombiniert Produkt- und Bestandsdaten fuer die API."""
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
    """Serialisiert eine Bestellung fuer das Admin-Dashboard."""
    return {
        **order,
        "orderId": str(order["orderId"]),
        "correlationId": str(order["correlationId"]),
        "amount": str(order["amount"]),
        "invoiceId": str(order["invoiceId"]) if order.get("invoiceId") else None,
    }


def _serialize_snapshot(snapshot: dict) -> dict:
    """Serialisiert UUIDs und Zeitstempel eines Audit-Snapshots."""
    return {
        **snapshot,
        "id": str(snapshot["id"]),
        "correlationId": str(snapshot["correlationId"]),
        "previousEventId": str(snapshot["previousEventId"]) if snapshot.get("previousEventId") else None,
        "timestamp": snapshot["timestamp"].isoformat(),
    }
