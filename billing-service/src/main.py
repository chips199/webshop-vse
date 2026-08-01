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
from .payment import PaymentFacadeError, get_payment_facade
from .problem_details import register_problem_handlers

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


def publish_payment_result(
        *,
        status: str,
        correlation_id: str,
        previous_event_id: str | None,
        order_id: str,
        transaction_id: str | None,
        provider: str,
        amount: str,
        currency: str,
        scenario: str = "happy_path",
        reason_code: str | None = None,
        message: str | None = None,
        customer: dict | None = None,
        shipping_address: dict | None = None,
) -> None:
    if status == "SUCCEEDED":
        event_type = "billing.payment.succeeded"
        event_payload = {
            "orderId": order_id,
            "transactionId": transaction_id,
            "provider": provider,
            "amount": amount,
            "currency": currency,
            "scenario": scenario,
            "paymentStatus": status,
        }
        # Nur gesetzt, wenn der Anbieter (mit Sandbox-Credentials) echte
        # Kaeufer-/Adressdaten zurueckgeliefert hat (siehe PaymentResult in
        # payment/models.py) - shop-service uebernimmt diese dann in die
        # Order und ueberschreibt damit die Checkout-Formular-Eingaben.
        if customer:
            event_payload["customer"] = customer
        if shipping_address:
            event_payload["shippingAddress"] = shipping_address
    else:
        event_type = "billing.payment.failed"
        payment_result = "TIMEOUT" if reason_code == "PAYMENT_TIMEOUT" else "DECLINED"
        event_payload = {
            "orderId": order_id,
            "provider": provider,
            "amount": amount,
            "currency": currency,
            "reasonCode": reason_code or "PAYMENT_DECLINED",
            "message": message or "Payment provider did not approve the payment.",
        }
        if transaction_id:
            event_payload["transactionId"] = transaction_id
    if status == "SUCCEEDED":
        payment_result = "SUCCEEDED"
    event = build_message(
        event_type,
        correlation_id,
        event_payload,
        previous_event_id=previous_event_id,
    )
    publish_message(event_type, event)
    logger.info(
        "Payment attempt finished",
        extra={
            "correlation_id": correlation_id,
            "context": {
                "eventType": event_type,
                "orderId": order_id,
                "provider": provider,
                "paymentStatus": status,
                "paymentResult": payment_result,
                "reasonCode": reason_code,
            },
        },
    )


def handle_billing_message(message: dict) -> None:
    if message["type"] == "billing.payment.confirm.requested":
        payload = message.get("payload", {})
        provider = payload.get("provider") or settings.payment_provider
        facade = get_payment_facade(provider)
        try:
            result = facade.get_status(payload["transactionId"], correlation_id=message["correlationId"])
        except PaymentFacadeError as exc:
            publish_payment_result(
                status="FAILED",
                correlation_id=message["correlationId"],
                previous_event_id=message["messageId"],
                order_id=payload["orderId"],
                transaction_id=payload.get("transactionId"),
                provider=provider,
                amount=payload["amount"],
                currency=payload["currency"],
                reason_code="PAYMENT_PROVIDER_ERROR",
                message=str(exc),
            )
            return
        if result.status.value != "SUCCEEDED":
            publish_payment_result(
                status="FAILED",
                correlation_id=message["correlationId"],
                previous_event_id=message["messageId"],
                order_id=payload["orderId"],
                transaction_id=result.transaction_id,
                provider=result.provider,
                amount=payload["amount"],
                currency=payload["currency"],
                reason_code="PAYMENT_DECLINED",
                message=result.reason or "Payment provider did not confirm the payment.",
            )
            return
        publish_payment_result(
            status=result.status.value,
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=result.transaction_id,
            provider=result.provider,
            amount=payload["amount"],
            currency=payload["currency"],
            customer=result.customer,
            shipping_address=result.shipping_address,
        )
        return

    if message["type"] == "billing.refund.requested":
        payload = message.get("payload", {})
        provider = payload.get("provider") or settings.payment_provider
        facade = get_payment_facade(provider)
        try:
            result = facade.refund(
                payload["transactionId"],
                Decimal(payload["amount"]),
                correlation_id=message["correlationId"],
            )
        except PaymentFacadeError as exc:
            logger.error(
                "Refund failed",
                extra={
                    "correlation_id": message["correlationId"],
                    "context": {
                        "eventType": "billing.refund.failed",
                        "orderId": payload.get("orderId"),
                        "transactionId": payload.get("transactionId"),
                        "provider": provider,
                        "error": str(exc),
                    },
                },
            )
            event = build_message(
                "billing.refund.failed",
                message["correlationId"],
                {
                    "orderId": payload["orderId"],
                    "transactionId": payload.get("transactionId"),
                    "provider": provider,
                    "amount": payload["amount"],
                    "currency": payload["currency"],
                    "reasonCode": "REFUND_PROVIDER_ERROR",
                    "message": str(exc),
                },
                previous_event_id=message["messageId"],
            )
            publish_message("billing.refund.failed", event)
            return
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
        publish_payment_result(
            status="FAILED",
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=None,
            provider=payload.get("provider") or settings.payment_provider,
            amount=payload["amount"],
            currency=payload["currency"],
            scenario=scenario,
            reason_code=reason_code,
            message="Payment-Stub simuliert eine fehlgeschlagene Zahlung.",
        )
        return

    provider = payload.get("provider") or settings.payment_provider
    facade = get_payment_facade(provider)
    payment = dict(payload.get("payment", {}))
    payment["scenario"] = scenario
    payment["correlationId"] = message["correlationId"]
    payment["previousEventId"] = message["messageId"]
    try:
        result = facade.charge(
            payload["orderId"],
            Decimal(payload["amount"]),
            payload["currency"],
            payment.get("testPaymentMethod"),
            payment,
        )
    except PaymentFacadeError as exc:
        publish_payment_result(
            status="FAILED",
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=None,
            provider=provider,
            amount=payload["amount"],
            currency=payload["currency"],
            reason_code="PAYMENT_PROVIDER_ERROR",
            message=str(exc),
        )
        return
    if result.status.value == "PENDING":
        event = build_message(
            "billing.payment.pending",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "transactionId": result.transaction_id,
                "provider": result.provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "paymentStatus": result.status.value,
                "redirectUrl": result.redirect_url,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.pending", event)
        logger.info(
            "Payment confirmation pending",
            extra={
                "correlation_id": message["correlationId"],
                "context": {
                    "eventType": "billing.payment.pending",
                    "orderId": payload["orderId"],
                    "provider": result.provider,
                    "paymentStatus": result.status.value,
                },
            },
        )
        return
    if result.status.value != "SUCCEEDED":
        publish_payment_result(
            status="FAILED",
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=result.transaction_id,
            provider=result.provider,
            amount=payload["amount"],
            currency=payload["currency"],
            reason_code="PAYMENT_DECLINED",
            message=result.reason or "Payment provider did not approve the payment.",
        )
        return
    publish_payment_result(
        status=result.status.value,
        correlation_id=message["correlationId"],
        previous_event_id=message["messageId"],
        order_id=payload["orderId"],
        transaction_id=result.transaction_id,
        provider=result.provider,
        amount=payload["amount"],
        currency=payload["currency"],
        scenario=scenario,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_thread
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(
            ["billing.payment.requested", "billing.payment.confirm.requested", "billing.refund.requested"],
            handle_billing_message,
            stop_consumer_event,
        ),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Billing command consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Billing Service API", version="0.1.0", lifespan=lifespan)
register_problem_handlers(app)
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


class AsyncPaymentWebhookRequest(BaseModel):
    orderId: str
    transactionId: str
    provider: str = "paypal"
    amount: str
    currency: str = "EUR"
    status: str
    correlationId: str
    previousEventId: str | None = None
    reasonCode: str | None = None
    message: str | None = None
    scenario: str = "async_webhook"


class AsyncPaymentWebhookResponse(BaseModel):
    accepted: bool = True
    eventType: str


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


@app.post("/webhooks/payment-stub", response_model=AsyncPaymentWebhookResponse)
async def receive_async_payment_webhook(request: AsyncPaymentWebhookRequest) -> AsyncPaymentWebhookResponse:
    status = request.status.upper()
    if status not in {"SUCCEEDED", "FAILED"}:
        raise HTTPException(status_code=400, detail="Unsupported async payment webhook status")
    publish_payment_result(
        status=status,
        correlation_id=request.correlationId,
        previous_event_id=request.previousEventId,
        order_id=request.orderId,
        transaction_id=request.transactionId,
        provider=request.provider,
        amount=request.amount,
        currency=request.currency,
        scenario=request.scenario,
        reason_code=request.reasonCode,
        message=request.message,
    )
    event_type = "billing.payment.succeeded" if status == "SUCCEEDED" else "billing.payment.failed"
    return AsyncPaymentWebhookResponse(eventType=event_type)