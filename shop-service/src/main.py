from contextlib import asynccontextmanager
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .catalog import calculate_total, enrich_items
from .config import settings
from .database import create_order as create_order_record
from .database import complete_order_if_ready
from .database import get_order as get_order_record
from .database import init_database, update_order_status
from .database import update_invoice_created, update_payment_succeeded, update_warehouse_commit
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


def handle_saga_message(message: dict) -> None:
    message_type = message["type"]
    payload = message.get("payload", {})
    correlation_id = message["correlationId"]

    if message_type == "warehouse.reservation.succeeded":
        order_id = payload["orderId"]
        update_order_status(order_id, "RESERVED")
        payment_requested = build_message(
            "billing.payment.requested",
            correlation_id,
            {
                "orderId": order_id,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "provider": payload["provider"],
                "scenario": payload.get("scenario", "happy_path"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.requested", payment_requested)
        update_order_status(order_id, "PAYMENT_PENDING")
        return

    if message_type == "warehouse.reservation.failed":
        update_order_status(payload["orderId"], "OUT_OF_STOCK")
        return

    if message_type == "billing.payment.succeeded":
        order_id = payload["orderId"]
        update_payment_succeeded(order_id, payload["transactionId"])

        invoice_requested = build_message(
            "invoice.create.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "scenario": payload.get("scenario", "happy_path"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("invoice.create.requested", invoice_requested)

        commit_requested = build_message(
            "warehouse.commit.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "scenario": payload.get("scenario", "happy_path"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.commit.requested", commit_requested)
        return

    if message_type == "billing.payment.failed":
        order_id = payload["orderId"]
        update_order_status(order_id, "PAYMENT_FAILED")
        cancel_requested = build_message(
            "warehouse.cancel.requested",
            correlation_id,
            {
                "orderId": order_id,
                "reasonCode": payload.get("reasonCode", "PAYMENT_FAILED"),
                "message": "Zahlung fehlgeschlagen, Warehouse-Reservierung wird storniert.",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.cancel.requested", cancel_requested)
        return

    if message_type == "warehouse.cancel.succeeded":
        update_order_status(payload["orderId"], "PAYMENT_FAILED")
        return

    if message_type == "invoice.created":
        order_id = payload["orderId"]
        update_invoice_created(order_id, payload["invoiceId"])
        maybe_publish_order_completed(order_id, correlation_id, message)
        return

    if message_type == "invoice.failed":
        update_order_status(payload["orderId"], "INVOICE_RETRY_PENDING")
        return

    if message_type == "warehouse.commit.succeeded":
        order_id = payload["orderId"]
        update_warehouse_commit(order_id, "SUCCEEDED")
        maybe_publish_order_completed(order_id, correlation_id, message)
        return

    if message_type == "warehouse.commit.failed":
        order_id = payload["orderId"]
        update_order_status(order_id, "REFUND_PENDING")
        refund_requested = build_message(
            "billing.refund.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "reasonCode": payload.get("reasonCode", "WAREHOUSE_COMMIT_FAILED"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.refund.requested", refund_requested)
        return

    if message_type == "billing.refund.succeeded":
        order_id = payload["orderId"]
        update_order_status(order_id, "ROLLBACK_COMPLETED")
        rollback_completed = build_message(
            "order.rollback.completed",
            correlation_id,
            {
                "orderId": order_id,
                "status": "ROLLBACK_COMPLETED",
                "transactionId": payload["transactionId"],
            },
            previous_event_id=message["messageId"],
        )
        publish_message("order.rollback.completed", rollback_completed)
        return

    if message_type == "billing.refund.failed":
        update_order_status(payload["orderId"], "REFUND_FAILED")


def maybe_publish_order_completed(order_id: str, correlation_id: str, previous_message: dict) -> None:
    if not complete_order_if_ready(order_id):
        return
    order_completed = build_message(
        "order.completed",
        correlation_id,
        {
            "orderId": order_id,
            "status": "COMPLETED",
        },
        previous_event_id=previous_message["messageId"],
    )
    publish_message("order.completed", order_completed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_thread
    init_database()
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(
            [
                "warehouse.reservation.succeeded",
                "warehouse.reservation.failed",
                "billing.payment.succeeded",
                "billing.payment.failed",
                "billing.refund.succeeded",
                "billing.refund.failed",
                "invoice.created",
                "invoice.failed",
                "warehouse.commit.succeeded",
                "warehouse.commit.failed",
                "warehouse.cancel.succeeded",
            ],
            handle_saga_message,
            stop_consumer_event,
        ),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Shop saga consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Historical Computer Parts Shop API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class OrderItem(BaseModel):
    productId: str
    quantity: int = Field(ge=1)


class PaymentSelection(BaseModel):
    provider: str
    currency: str = "EUR"
    scenario: str = "happy_path"


class CreateOrderRequest(BaseModel):
    customerId: str
    items: list[OrderItem] = Field(min_length=1)
    payment: PaymentSelection


class OrderResponse(BaseModel):
    orderId: str
    correlationId: str
    status: str
    amount: str | None = None
    currency: str | None = None


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
    raw_items = [item.model_dump() for item in order.items]
    enriched_items = enrich_items(raw_items)
    amount = calculate_total(raw_items)
    currency = order.payment.currency
    payment = order.payment.model_dump()
    create_order_record(
        order_id,
        correlation_id,
        order.customerId,
        enriched_items,
        payment,
        str(amount),
        currency,
    )
    order_created = build_message(
        "order.created",
        correlation_id,
        {
            "orderId": order_id,
            "customerId": order.customerId,
            "items": enriched_items,
            "payment": payment,
            "amount": str(amount),
            "currency": currency,
            "status": "PENDING",
        },
    )
    publish_message("order.created", order_created)

    reserve_requested = build_message(
        "warehouse.reserve.requested",
        correlation_id,
        {
            "orderId": order_id,
            "items": enriched_items,
            "amount": str(amount),
            "currency": currency,
            "provider": order.payment.provider,
            "scenario": order.payment.scenario,
        },
        previous_event_id=order_created["messageId"],
    )
    publish_message("warehouse.reserve.requested", reserve_requested)

    logger.info(
        "Order accepted for asynchronous processing",
        extra={
            "correlation_id": correlation_id,
            "context": {"orderId": order_id, "itemCount": len(order.items)},
        },
    )
    return OrderResponse(
        orderId=order_id,
        correlationId=correlation_id,
        status="PENDING",
        amount=str(amount),
        currency=currency,
    )


@app.get("/orders/{orderId}", response_model=OrderResponse)
async def get_order(orderId: str) -> OrderResponse:
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    return OrderResponse(
        orderId=str(order["orderId"]),
        correlationId=str(order["correlationId"]),
        status=order["status"],
        amount=str(order["amount"]),
        currency=order["currency"],
    )
