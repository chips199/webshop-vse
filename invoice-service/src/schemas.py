"""API-Datenmodelle des Invoice-Service."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Antwort des Health-Endpunkts."""

    status: str = "ok"
    service: str


class InvoiceResponse(BaseModel):
    """Metadaten einer Rechnung."""

    invoiceId: str
    orderId: str
    correlationId: str
    status: str
    pdfPath: str | None = None
    downloadUrl: str | None = None
    attempts: int
    lastError: str | None = None
