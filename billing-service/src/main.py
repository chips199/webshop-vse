from contextlib import asynccontextmanager
from decimal import Decimal
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
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
    result = facade.charge(payload["orderId"], Decimal(payload["amount"]), payload["currency"])
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


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class PaymentStatusResponse(BaseModel):
    transactionId: str
    provider: str
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


@app.get("/payments/{transactionId}/status", response_model=PaymentStatusResponse)
async def get_payment_status(transactionId: str) -> PaymentStatusResponse:
    facade = get_payment_facade(settings.payment_provider)
    result = facade.get_status(transactionId)
    return PaymentStatusResponse(
        transactionId=result.transaction_id,
        provider=result.provider,
        status=result.status.value,
    )
