"""API-Datenmodelle des Warehouse-Service."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Antwort des Health-Endpunkts."""

    status: str = "ok"
    service: str


class StockResponse(BaseModel):
    """Bestandsdaten eines Produkts."""

    productId: str
    quantityOnHand: int
    reservedQuantity: int
    availableQuantity: int
    location: str


class StockUpdateRequest(BaseModel):
    """Aenderung eines Lagerbestands."""

    quantityOnHand: int = Field(ge=0)
    location: str | None = None


class StockCreateRequest(StockUpdateRequest):
    """Neuer Lagerbestand."""

    productId: str
