"""HTTP-Endpunkte des Billing-Service."""

from fastapi import APIRouter, HTTPException

from .config import settings
from .payment import get_payment_facade
from .schemas import (
    AsyncPaymentWebhookRequest,
    AsyncPaymentWebhookResponse,
    HealthResponse,
    PaymentStatusResponse,
)
from .service import publish_payment_result

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness- und Readiness-Status."""
    return HealthResponse(service=settings.service_name)


@router.get("/payments/{transactionId}/status", response_model=PaymentStatusResponse)
async def get_payment_status(transactionId: str) -> PaymentStatusResponse:
    """Liest den aktuellen Status einer Transaktion."""
    facade = get_payment_facade(settings.payment_provider)
    result = facade.get_status(transactionId)
    return PaymentStatusResponse(
        transactionId=result.transaction_id,
        provider=result.provider,
        status=result.status.value,
    )


@router.post("/webhooks/payment-stub", response_model=AsyncPaymentWebhookResponse)
async def receive_async_payment_webhook(request: AsyncPaymentWebhookRequest) -> AsyncPaymentWebhookResponse:
    """Verarbeitet den Callback des PayPal-Stubs."""
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
