"""Pydantic-Schemas (Request-/Response-Modelle) des warehouse-service."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Antwort des /health-Endpunkts (fuer Docker-Healthchecks/Monitoring)."""

    status: str = "ok"
    service: str


class StockResponse(BaseModel):
    """Bestandsdatensatz eines Produkts, wie er nach aussen (REST) sichtbar ist."""

    productId: str
    quantityOnHand: int
    reservedQuantity: int
    availableQuantity: int
    location: str


class StockUpdateRequest(BaseModel):
    """Body fuer PATCH /stock/{productId} (Admin-Bestandspflege)."""

    quantityOnHand: int = Field(ge=0)
    location: str | None = None


class StockCreateRequest(StockUpdateRequest):
    """Body fuer POST /stock (neues Produkt im Lager anlegen)."""

    productId: str
