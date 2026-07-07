from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from .config import settings
from .logging_config import configure_logging

configure_logging()

app = FastAPI(title="Invoice Service API", version="0.1.0")


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class InvoiceResponse(BaseModel):
    invoiceId: str
    orderId: str
    correlationId: str
    status: str
    pdfPath: str | None = None


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


@app.get("/invoices/{invoiceId}", response_model=InvoiceResponse)
async def get_invoice(invoiceId: str) -> InvoiceResponse:
    raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
