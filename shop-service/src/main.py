from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets
import threading
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import settings
from .database import calculate_total, create_admin_session, delete_admin_session
from .database import create_order as create_order_record
from .database import enrich_items_from_products, get_admin_session, get_audit_snapshots_for_order, get_products
from .database import complete_order_if_ready
from .database import list_admin_orders, verify_admin_credentials
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
                "payment": payload.get("payment", {}),
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)

ADMIN_SESSION_COOKIE = "admin_session"


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class OrderItem(BaseModel):
    productId: str
    quantity: int = Field(ge=1)


class Customer(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str | None = None


class Address(BaseModel):
    street: str = Field(min_length=1)
    houseNumber: str = Field(min_length=1)
    postalCode: str = Field(min_length=1)
    city: str = Field(min_length=1)
    country: str = Field(min_length=1)


class PaymentSelection(BaseModel):
    provider: str
    currency: str = "EUR"
    scenario: str = "happy_path"
    mode: str = "sandbox"
    cardholder: str | None = None
    testPaymentMethod: str | None = None
    paypalEmail: str | None = None


class CreateOrderRequest(BaseModel):
    customerId: str | None = None
    customer: Customer
    shippingAddress: Address
    billingAddress: Address | None = None
    items: list[OrderItem] = Field(min_length=1)
    payment: PaymentSelection


class ProductResponse(BaseModel):
    id: str
    name: str
    year: str
    description: str
    price: str
    currency: str
    imageUrl: str
    imageAlt: str
    imageSource: str
    imageLicense: str
    imageCredit: str


class OrderResponse(BaseModel):
    orderId: str
    correlationId: str
    status: str
    amount: str | None = None
    currency: str | None = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminSessionResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class AdminOrderResponse(BaseModel):
    orderId: str
    correlationId: str
    status: str
    amount: str
    currency: str
    customer: dict | None = None
    shippingAddress: dict | None = None
    transactionId: str | None = None
    invoiceId: str | None = None
    invoiceStatus: str | None = None
    warehouseCommitStatus: str | None = None
    createdAt: datetime
    updatedAt: datetime


class AdminAuditResponse(BaseModel):
    orderId: str
    snapshots: list[dict]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_admin(request: Request) -> str:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    session = get_admin_session(_token_hash(token))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    return session["username"]


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


@app.get("/products", response_model=list[ProductResponse])
async def list_products() -> list[ProductResponse]:
    return [ProductResponse(**_serialize_product(product)) for product in get_products()]


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_order(request: Request, order: CreateOrderRequest) -> OrderResponse:
    correlation_id = request.state.correlation_id
    order_id = str(uuid4())
    customer_id = order.customerId or str(uuid4())
    raw_items = [item.model_dump() for item in order.items]
    try:
        enriched_items = enrich_items_from_products(raw_items)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    amount = calculate_total(enriched_items)
    currency = order.payment.currency
    payment = order.payment.model_dump()
    create_order_record(
        order_id,
        correlation_id,
        customer_id,
        order.customer.model_dump(),
        order.shippingAddress.model_dump(),
        order.billingAddress.model_dump() if order.billingAddress else None,
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
            "customerId": customer_id,
            "customer": order.customer.model_dump(),
            "shippingAddress": order.shippingAddress.model_dump(),
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
            "payment": payment,
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


@app.post("/admin/login", response_model=AdminSessionResponse)
async def admin_login(credentials: AdminLoginRequest, response: Response) -> AdminSessionResponse:
    if not verify_admin_credentials(credentials.username, credentials.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.admin_session_hours)
    create_admin_session(_token_hash(token), credentials.username, expires_at)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="lax",
        max_age=settings.admin_session_hours * 60 * 60,
        path="/",
    )
    return AdminSessionResponse(authenticated=True, username=credentials.username)


@app.post("/admin/logout", response_model=AdminSessionResponse)
async def admin_logout(request: Request, response: Response) -> AdminSessionResponse:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token:
        delete_admin_session(_token_hash(token))
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return AdminSessionResponse(authenticated=False)


@app.get("/admin/session", response_model=AdminSessionResponse)
async def admin_session(username: str = Depends(require_admin)) -> AdminSessionResponse:
    return AdminSessionResponse(authenticated=True, username=username)


@app.get("/admin/orders", response_model=list[AdminOrderResponse])
async def admin_orders(_: str = Depends(require_admin)) -> list[AdminOrderResponse]:
    return [AdminOrderResponse(**_serialize_order(order)) for order in list_admin_orders()]


@app.get("/admin/orders/{orderId}/audit", response_model=AdminAuditResponse)
async def admin_order_audit(orderId: str, _: str = Depends(require_admin)) -> AdminAuditResponse:
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    return AdminAuditResponse(
        orderId=orderId,
        snapshots=[_serialize_snapshot(snapshot) for snapshot in get_audit_snapshots_for_order(orderId)],
    )


def _serialize_product(product: dict) -> dict:
    return {
        **product,
        "id": str(product["id"]),
        "price": str(product["price"]),
    }


def _serialize_order(order: dict) -> dict:
    return {
        **order,
        "orderId": str(order["orderId"]),
        "correlationId": str(order["correlationId"]),
        "amount": str(order["amount"]),
        "invoiceId": str(order["invoiceId"]) if order.get("invoiceId") else None,
    }


def _serialize_snapshot(snapshot: dict) -> dict:
    return {
        **snapshot,
        "id": str(snapshot["id"]),
        "correlationId": str(snapshot["correlationId"]),
        "previousEventId": str(snapshot["previousEventId"]) if snapshot.get("previousEventId") else None,
        "timestamp": snapshot["timestamp"].isoformat(),
    }
