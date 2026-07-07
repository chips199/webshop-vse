import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .config import settings
from .logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Shop Service API", version="0.1.0")


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class OrderItem(BaseModel):
    productId: str
    quantity: int = Field(ge=1)


class PaymentSelection(BaseModel):
    provider: str
    currency: str = "EUR"


class CreateOrderRequest(BaseModel):
    customerId: str
    items: list[OrderItem] = Field(min_length=1)
    payment: PaymentSelection


class OrderResponse(BaseModel):
    orderId: str
    correlationId: str
    status: str


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(service=settings.service_name)


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_order(request: Request, order: CreateOrderRequest) -> OrderResponse:
    correlation_id = request.state.correlation_id
    order_id = str(uuid4())
    logger.info(
        "Order accepted for asynchronous processing",
        extra={
            "correlation_id": correlation_id,
            "context": {"orderId": order_id, "itemCount": len(order.items)},
        },
    )
    return OrderResponse(orderId=order_id, correlationId=correlation_id, status="PENDING")


@app.get("/orders/{orderId}", response_model=OrderResponse)
async def get_order(orderId: str) -> OrderResponse:
    raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
