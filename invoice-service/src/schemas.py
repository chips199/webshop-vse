"""Pydantic-Schemas (Request-/Response-Modelle) des invoice-service."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Antwort des /health-Endpunkts (fuer Docker-Healthchecks/Monitoring)."""

    status: str = "ok"
    service: str


class InvoiceResponse(BaseModel):
    """Metadaten einer Rechnung, wie sie ueber GET /invoices/{invoiceId} geliefert werden."""

    invoiceId: str
    orderId: str
    correlationId: str
    status: str
    pdfPath: str | None = None
    downloadUrl: str | None = None
    attempts: int
    lastError: str | None = None
