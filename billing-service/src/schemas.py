"""Pydantic-Schemas (Request-/Response-Modelle) des billing-service.

Reine Datenklassen ohne Verhalten - Validierung/Serialisierung fuer die
HTTP-Schicht (routes.py). Enthaelt bewusst keine Business-Logik; die liegt
in service.py.
"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Antwort von GET /health - fuer Docker-Healthchecks/Monitoring."""

    status: str = "ok"
    service: str


class PaymentStatusResponse(BaseModel):
    """Antwort von GET /payments/{transactionId}/status."""

    transactionId: str
    provider: str
    status: str


class AsyncPaymentWebhookRequest(BaseModel):
    """Body des internen PayPal-Stub-Webhooks (POST /webhooks/payment-stub).

    Wird ausschliesslich vom PayPalAdapter selbst geschickt (siehe
    _send_webhook() in payment/adapters.py), simuliert damit den
    asynchronen Webhook-Callback eines echten Zahlungsanbieters.
    """

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
    """Antwort von POST /webhooks/payment-stub (nur Bestaetigung des Empfangs)."""

    accepted: bool = True
    eventType: str
