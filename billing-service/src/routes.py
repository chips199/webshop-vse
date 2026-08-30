"""HTTP-Router (Router-Schicht) des billing-service.

Duenne FastAPI-Endpunkte: validieren/deserialisieren den Request (ueber
schemas.py), delegieren die eigentliche Arbeit an service.py bzw. die
PaymentFacade, und serialisieren das Ergebnis zurueck. Enthaelt selbst keine
Business-Logik.
"""

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
    """Einfacher Liveness-/Readiness-Check fuer Docker Compose/Monitoring."""
    return HealthResponse(service=settings.service_name)


@router.get("/payments/{transactionId}/status", response_model=PaymentStatusResponse)
async def get_payment_status(transactionId: str) -> PaymentStatusResponse:
    """Fragt synchron den aktuellen Status einer Transaktion ab (Debug-/Admin-Zweck).

    Nutzt denselben Adapter-Retry wie der Saga-Confirm-Pfad in
    handle_billing_message(); eine dauerhaft fehlschlagende Statusabfrage
    fuehrt hier (mangels eigenem try/except) zu einer unbehandelten
    PaymentFacadeError, die vom generischen Exception-Handler in
    problem_details.py als 500 beantwortet wird.
    """
    facade = get_payment_facade(settings.payment_provider)
    result = facade.get_status(transactionId)
    return PaymentStatusResponse(
        transactionId=result.transaction_id,
        provider=result.provider,
        status=result.status.value,
    )


@router.post("/webhooks/payment-stub", response_model=AsyncPaymentWebhookResponse)
async def receive_async_payment_webhook(request: AsyncPaymentWebhookRequest) -> AsyncPaymentWebhookResponse:
    """Empfaengt den asynchronen Webhook-Callback des PayPal-Stubs.

    Wird ausschliesslich intern vom PayPalAdapter selbst aufgerufen (siehe
    _schedule_webhook()/_send_webhook() in payment/adapters.py), simuliert
    also den Callback, den ein echter Zahlungsanbieter nach verzoegerter
    Zahlungsbestaetigung schicken wuerde. Uebersetzt das Ergebnis in das
    passende billing.payment.succeeded/.failed-Saga-Event.
    """
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
