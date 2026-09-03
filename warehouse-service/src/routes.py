"""HTTP-Endpunkte des Warehouse-Service."""

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
    """Liefert den gesamten Lagerbestand."""
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
    """Legt einen Lagerbestand an oder aktualisiert ihn."""
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
    """Aktualisiert Menge und Lagerort eines Produkts."""
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
