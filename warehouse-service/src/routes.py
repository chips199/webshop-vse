"""HTTP-Router (Router-Schicht) des warehouse-service.

Synchrone REST-Endpunkte fuer Lagerbestand (GET/POST/PATCH /stock), die z.B.
vom Shop-Service fuer die Produktkatalog-Anzeige und vom Admin-Dashboard fuer
die Bestandspflege genutzt werden. Reine CRUD-Weiterleitung an die
Repository-Schicht (database.py) - keine eigene Service-Funktion noetig, da
hier ausser Serialisierung/Statuscode-Mapping keine Business-Logik anfaellt.
"""

from fastapi import APIRouter, HTTPException

from .config import settings
from .database import create_stock, list_stock, update_stock
from .schemas import HealthResponse, StockCreateRequest, StockResponse, StockUpdateRequest

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(service=settings.service_name)


@router.get("/stock", response_model=list[StockResponse])
async def stock() -> list[StockResponse]:
    """Liefert den kompletten Lagerbestand (z.B. fuer Produktkatalog/Admin-Dashboard)."""
    return [
        StockResponse(
            productId=str(entry["productId"]),
            quantityOnHand=entry["quantityOnHand"],
            reservedQuantity=entry["reservedQuantity"],
            availableQuantity=entry["availableQuantity"],
            location=entry["location"],
        )
        for entry in list_stock()
    ]


@router.post("/stock", response_model=StockResponse)
async def post_stock(request: StockCreateRequest) -> StockResponse:
    """Legt einen neuen Lagerbestand-Eintrag an oder aktualisiert ihn
    (create_stock() ist idempotent via ON CONFLICT, siehe database.py)."""
    created = create_stock(request.productId, request.quantityOnHand, request.location)
    return StockResponse(
        productId=str(created["productId"]),
        quantityOnHand=created["quantityOnHand"],
        reservedQuantity=created["reservedQuantity"],
        availableQuantity=created["availableQuantity"],
        location=created["location"],
    )


@router.patch("/stock/{productId}", response_model=StockResponse)
async def patch_stock(productId: str, request: StockUpdateRequest) -> StockResponse:
    """Aktualisiert quantityOnHand/location eines bestehenden Produkts (Admin-Pflege).

    409, falls die neue Menge unter die bereits reservierte Menge faellt
    (siehe ValueError in update_stock()); 404, falls das Produkt nicht existiert.
    """
    try:
        updated = update_stock(productId, request.quantityOnHand, request.location)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Stock entry for product {productId} not found")
    return StockResponse(
        productId=str(updated["productId"]),
        quantityOnHand=updated["quantityOnHand"],
        reservedQuantity=updated["reservedQuantity"],
        availableQuantity=updated["availableQuantity"],
        location=updated["location"],
    )
