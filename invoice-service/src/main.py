from contextlib import asynccontextmanager
import logging
from pathlib import Path
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from .config import settings
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None
invoice_store: dict[str, dict] = {}
invoice_dir = Path("invoices")


def handle_invoice_message(message: dict) -> None:
    if message["type"] != "invoice.create.requested":
        return
    payload = message.get("payload", {})
    invoice_id = str(uuid4())
    invoice_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = invoice_dir / f"{invoice_id}.txt"
    invoice_path.write_text(
        "\n".join(
            [
                "Rechnung fuer historische Computerteile",
                f"Invoice ID: {invoice_id}",
                f"Order ID: {payload['orderId']}",
                f"Transaction ID: {payload['transactionId']}",
                f"Amount: {payload['amount']} {payload['currency']}",
                f"Payment Provider: {payload['provider']}",
            ]
        ),
        encoding="utf-8",
    )
    invoice_store[invoice_id] = {
        "invoiceId": invoice_id,
        "orderId": payload["orderId"],
        "correlationId": message["correlationId"],
        "status": "CREATED",
        "pdfPath": str(invoice_path),
    }
    event = build_message(
        "invoice.created",
        message["correlationId"],
        {
            "invoiceId": invoice_id,
            "orderId": payload["orderId"],
            "transactionId": payload["transactionId"],
            "status": "CREATED",
            "pdfPath": str(invoice_path),
        },
        previous_event_id=message["messageId"],
    )
    publish_message("invoice.created", event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_thread
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(["invoice.create.requested"], handle_invoice_message, stop_consumer_event),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Invoice command consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Invoice Service API", version="0.1.0", lifespan=lifespan)


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
    invoice = invoice_store.get(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    return InvoiceResponse(**invoice)
