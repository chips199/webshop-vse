from contextlib import asynccontextmanager
from decimal import Decimal
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message
from .payment import get_payment_facade

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


def handle_billing_message(message: dict) -> None:
    if message["type"] == "billing.refund.requested":
        payload = message.get("payload", {})
        provider = payload.get("provider") or settings.payment_provider
        facade = get_payment_facade(provider)
        result = facade.refund(payload["transactionId"], Decimal(payload["amount"]))
        event = build_message(
            "billing.refund.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "transactionId": result.transaction_id,
                "provider": result.provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "refundStatus": result.status.value,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.refund.succeeded", event)
        return

    if message["type"] != "billing.payment.requested":
        return
    payload = message.get("payload", {})
    scenario = payload.get("scenario", "happy_path")
    if scenario in {"payment_failed", "payment_timeout"}:
        reason_code = "PAYMENT_TIMEOUT" if scenario == "payment_timeout" else "PAYMENT_DECLINED"
        event = build_message(
            "billing.payment.failed",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "provider": payload.get("provider") or settings.payment_provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "reasonCode": reason_code,
                "message": "Payment-Stub simuliert eine fehlgeschlagene Zahlung.",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.failed", event)
        return

    provider = payload.get("provider") or settings.payment_provider
    facade = get_payment_facade(provider)
    payment = payload.get("payment", {})
    try:
        result = facade.charge(
            payload["orderId"],
            Decimal(payload["amount"]),
            payload["currency"],
            payment.get("testPaymentMethod"),
            payment,
        )
    except RuntimeError as exc:
        event = build_message(
            "billing.payment.failed",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "provider": provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "reasonCode": "PAYMENT_PROVIDER_ERROR",
                "message": str(exc),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.failed", event)
        return
    if result.status.value != "SUCCEEDED":
        event = build_message(
            "billing.payment.failed",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "provider": result.provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "reasonCode": "PAYMENT_DECLINED",
                "message": result.reason or "Payment provider did not approve the payment.",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.failed", event)
        return
    event_type = "billing.payment.succeeded"
    event_payload = {
        "orderId": payload["orderId"],
        "transactionId": result.transaction_id,
        "provider": result.provider,
        "amount": payload["amount"],
        "currency": payload["currency"],
        "scenario": scenario,
        "paymentStatus": result.status.value,
    }
    event = build_message(
        event_type,
        message["correlationId"],
        event_payload,
        previous_event_id=message["messageId"],
    )
    publish_message(event_type, event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_thread
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(["billing.payment.requested", "billing.refund.requested"], handle_billing_message, stop_consumer_event),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Billing command consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Billing Service API", version="0.1.0", lifespan=lifespan)
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


class PaymentStatusResponse(BaseModel):
    transactionId: str
    provider: str
    status: str


class PayPalCreateOrderRequest(BaseModel):
    referenceId: str
    amount: str
    currency: str = "EUR"
    returnUrl: str | None = None
    cancelUrl: str | None = None


class PayPalCreateOrderResponse(BaseModel):
    orderId: str
    status: str
    approveUrl: str | None = None
    stub: bool = False


class PayPalCaptureResponse(BaseModel):
    orderId: str
    captureId: str
    status: str
    payer: dict | None = None
    shippingAddress: dict | None = None
    stub: bool = False


class StripeCheckoutItem(BaseModel):
    name: str
    amount: str
    quantity: int = 1


class StripeCreateSessionRequest(BaseModel):
    referenceId: str
    amount: str
    currency: str = "EUR"
    successUrl: str | None = None
    cancelUrl: str | None = None
    customerEmail: str | None = None
    items: list[StripeCheckoutItem] | None = None


class StripeCreateSessionResponse(BaseModel):
    sessionId: str
    status: str
    paymentStatus: str
    checkoutUrl: str | None = None
    stub: bool = False


class StripeSessionResponse(BaseModel):
    sessionId: str
    status: str | None = None
    paymentStatus: str | None = None
    customer: dict | None = None
    shippingAddress: dict | None = None
    stub: bool = False


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


@app.get("/payments/{transactionId}/status", response_model=PaymentStatusResponse)
async def get_payment_status(transactionId: str) -> PaymentStatusResponse:
    facade = get_payment_facade(settings.payment_provider)
    result = facade.get_status(transactionId)
    return PaymentStatusResponse(
        transactionId=result.transaction_id,
        provider=result.provider,
        status=result.status.value,
    )


@app.post("/paypal/orders", response_model=PayPalCreateOrderResponse)
async def create_paypal_order(request: PayPalCreateOrderRequest) -> PayPalCreateOrderResponse:
    facade = get_payment_facade("paypal")
    try:
        result = facade.create_paypal_order(
            request.referenceId,
            Decimal(request.amount),
            request.currency,
            request.returnUrl,
            request.cancelUrl,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return PayPalCreateOrderResponse(**result)


@app.post("/paypal/orders/{paypalOrderId}/capture", response_model=PayPalCaptureResponse)
async def capture_paypal_order(paypalOrderId: str) -> PayPalCaptureResponse:
    facade = get_payment_facade("paypal")
    try:
        result = facade.capture_paypal_order(paypalOrderId)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return PayPalCaptureResponse(**result)


@app.post("/stripe/sessions", response_model=StripeCreateSessionResponse)
async def create_stripe_session(request: StripeCreateSessionRequest) -> StripeCreateSessionResponse:
    facade = get_payment_facade("stripe")
    try:
        result = facade.create_stripe_session(
            request.referenceId,
            Decimal(request.amount),
            request.currency,
            request.successUrl,
            request.cancelUrl,
            request.customerEmail,
            [item.model_dump() for item in request.items] if request.items else None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StripeCreateSessionResponse(**result)


@app.get("/stripe/sessions/{sessionId}", response_model=StripeSessionResponse)
async def get_stripe_session(sessionId: str) -> StripeSessionResponse:
    facade = get_payment_facade("stripe")
    try:
        result = facade.retrieve_stripe_session(sessionId)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StripeSessionResponse(**result)
