"""API-Datenmodelle des Billing-Service."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Antwort des Health-Endpunkts."""

    status: str = "ok"
    service: str


class PaymentStatusResponse(BaseModel):
    """Status einer Zahlung."""

    transactionId: str
    provider: str
    status: str


class AsyncPaymentWebhookRequest(BaseModel):
    """Payload des internen PayPal-Stub-Webhooks."""

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
    """Bestaetigung des Webhook-Empfangs."""

    accepted: bool = True
    eventType: str
