from contextlib import asynccontextmanager
import logging
from pathlib import Path
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings
from .database import get_invoice as get_invoice_record
from .database import init_database, mark_invoice_created, mark_invoice_failed, upsert_invoice_processing
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message
from .problem_details import register_problem_handlers

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None
invoice_dir = Path(settings.invoice_output_dir)


def handle_invoice_message(message: dict) -> None:
    """Verarbeitet EIN "invoice.create.requested"-Command mit genau einem Versuch.

    Wichtig: invoice-service selbst wiederholt NICHT mehr intern (frueher gab es
    hier eine 3-malige Retry-Schleife samt "invoice.retry.scheduled"). Die
    Retry-Orchestrierung gehoert fachlich in die Shop-Saga: nur shop-service kennt
    den Zustand des zugehoerigen Circuit Breakers (Bonusaufgabe 4.1) und kann
    entscheiden, ob/wann ein weiterer Versuch sinnvoll ist. Schlaegt die
    Rechnungserstellung hier fehl, wird deshalb sofort (nach genau einem Versuch)
    "invoice.failed" veroeffentlicht - inklusive "attempt" und der fachlichen
    Zahlungsdaten (provider/amount/currency/scenario), damit shop-service daraus
    bei Bedarf einen neuen "invoice.create.requested" mit attempt+1 bauen kann,
    ohne dass invoice-service selbst Retry-Zustand halten muss.
    """
    if message["type"] != "invoice.create.requested":
        return

    payload = message.get("payload", {})
    invoice_id = str(uuid4())
    # attempt kommt von shop-service (1 beim Erstversuch, sonst hochgezaehlt) -
    # invoice-service nutzt den Wert nur zum Protokollieren/Speichern, nicht
    # fuer eigene Retry-Entscheidungen.
    attempt = payload.get("attempt", 1)

    try:
        upsert_invoice_processing(invoice_id, message["correlationId"], payload, attempt)
        if payload.get("scenario") == "invoice_failed":
            raise RuntimeError("Rechnungserstellung wurde fuer das Fehlerszenario gezielt abgelehnt.")

        invoice_path = create_invoice_pdf(invoice_id, message["correlationId"], payload)
        mark_invoice_created(invoice_id, str(invoice_path), attempt)
        event = build_message(
            "invoice.created",
            message["correlationId"],
            {
                "invoiceId": invoice_id,
                "orderId": payload["orderId"],
                "transactionId": payload["transactionId"],
                "status": "CREATED",
                "pdfPath": str(invoice_path),
                "attempts": attempt,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("invoice.created", event)
    except Exception as exc:
        last_error = str(exc)
        logger.warning(
            "Invoice creation attempt failed",
            extra={
                "correlation_id": message["correlationId"],
                "context": {"orderId": payload.get("orderId"), "attempt": attempt, "error": last_error},
            },
        )
        mark_invoice_failed(invoice_id, last_error, attempt)
        failed_event = build_message(
            "invoice.failed",
            message["correlationId"],
            {
                "invoiceId": invoice_id,
                "orderId": payload["orderId"],
                "transactionId": payload["transactionId"],
                "provider": payload.get("provider"),
                "amount": payload.get("amount"),
                "currency": payload.get("currency"),
                "scenario": payload.get("scenario", "happy_path"),
                "attempt": attempt,
                "reasonCode": "INVOICE_RENDER_FAILED",
                "message": "Rechnungserstellung fehlgeschlagen.",
                "lastError": last_error,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("invoice.failed", failed_event)


def create_invoice_pdf(invoice_id: str, correlation_id: str, payload: dict) -> Path:
    invoice_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = invoice_dir / f"{invoice_id}.pdf"
    invoice_path.write_bytes(render_pdf(build_invoice_lines(invoice_id, correlation_id, payload)))
    return invoice_path


def build_invoice_lines(invoice_id: str, correlation_id: str, payload: dict) -> list[str]:
    customer = payload.get("customer") or {}
    shipping_address = payload.get("shippingAddress") or {}
    billing_address = payload.get("billingAddress") or shipping_address
    items = payload.get("items") or []
    lines = [
        "RETRO PARTS TERMINAL",
        "Rechnung fuer historische Computerteile",
        f"Rechnungsnummer: {invoice_id}",
        f"Bestellung: {payload['orderId']}",
        f"Transaktion: {payload['transactionId']}",
        f"Zahlungsanbieter: {payload['provider']}",
        "",
        "Kunde",
        _format_customer(customer),
        f"E-Mail: {customer.get('email', '-')}",
        f"Telefon: {customer.get('phone') or '-'}",
        "",
        "Lieferanschrift",
        *_format_address(shipping_address),
        "",
        "Rechnungsanschrift",
        *_format_address(billing_address),
        "",
        "Positionen",
        "Menge  Artikel                                      Einzel       Summe",
        *[_format_invoice_item(item, payload["currency"]) for item in items],
        "",
        f"Gesamtbetrag: {_money(payload['amount'], payload['currency'])}",
        f"Correlation-ID: {correlation_id}",
        "",
        "Vielen Dank fuer deinen Einkauf im Retro Parts Terminal.",
    ]
    return lines


def _format_customer(customer: dict) -> str:
    name = " ".join(
        value for value in [customer.get("firstName", "").strip(), customer.get("lastName", "").strip()] if value
    )
    return name or "-"


def _format_address(address: dict) -> list[str]:
    if not address:
        return ["-"]
    street = " ".join(
        value for value in [str(address.get("street", "")).strip(), str(address.get("houseNumber", "")).strip()] if value
    )
    city = " ".join(
        value for value in [str(address.get("postalCode", "")).strip(), str(address.get("city", "")).strip()] if value
    )
    country = str(address.get("country", "")).strip()
    return [line for line in [street, city, country] if line] or ["-"]


def _format_invoice_item(item: dict, fallback_currency: str) -> str:
    quantity = int(item.get("quantity", 0))
    name = str(item.get("name") or item.get("productId") or "Artikel")
    unit_price = _money(item.get("unitPrice", "0.00"), item.get("currency") or fallback_currency)
    line_total = _money(item.get("lineTotal", "0.00"), item.get("currency") or fallback_currency)
    return f"{quantity:>5}  {_truncate(name, 42):<42}  {unit_price:>10}  {line_total:>10}"


def _money(amount, currency: str) -> str:
    return f"{amount} {currency}"


def _truncate(value: str, max_length: int) -> str:
    return value if len(value) <= max_length else f"{value[: max_length - 3]}..."


def render_pdf(lines: list[str]) -> bytes:
    escaped_lines = [_pdf_escape(line) for line in lines]
    content_lines = [
        "0.02 0.08 0.02 rg",
        "0 0 595 842 re f",
        "0.44 0.94 0.36 RG",
        "2 w",
        "36 36 523 770 re S",
        "0.73 1 0.44 rg",
        "BT /F1 26 Tf 54 760 Td (RETRO PARTS TERMINAL) Tj ET",
        "0.44 0.94 0.36 rg",
        "BT /F1 13 Tf 54 735 Td (Historische Computerteile // Rechnung) Tj ET",
        "0.73 1 0.44 rg",
    ]
    y = 680
    for index, line in enumerate(escaped_lines):
        if y < 70:
            break
        font_size = 15 if index < 2 else 10
        content_lines.append(f"BT /F1 {font_size} Tf 54 {y} Td ({line}) Tj ET")
        y -= 18 if line else 12
    content_lines.extend(
        [
            "0.44 0.94 0.36 rg",
            "BT /F1 10 Tf 54 82 Td (Automatisch erzeugt durch den Invoice-Service.) Tj ET",
        ]
    )
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _pdf_escape(value: str) -> str:
    normalized = value.encode("latin-1", errors="replace").decode("latin-1")
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_thread
    init_database()
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
register_problem_handlers(app)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class InvoiceResponse(BaseModel):
    invoiceId: str
    orderId: str
    correlationId: str
    status: str
    pdfPath: str | None = None
    downloadUrl: str | None = None
    attempts: int
    lastError: str | None = None


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
    invoice = get_invoice_record(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    return InvoiceResponse(**_serialize_invoice(invoice))


@app.get("/invoices/{invoiceId}/pdf")
async def download_invoice_pdf(invoiceId: str) -> FileResponse:
    invoice = get_invoice_record(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    if invoice.get("status") != "CREATED" or not invoice.get("pdfPath"):
        raise HTTPException(status_code=409, detail=f"Invoice {invoiceId} is not ready for download")
    pdf_path = Path(invoice["pdfPath"])
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail=f"Invoice PDF for {invoiceId} not found")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"retro-parts-invoice-{invoiceId}.pdf",
    )


def _serialize_invoice(invoice: dict) -> dict:
    invoice_id = str(invoice["invoiceId"])
    return {
        **invoice,
        "invoiceId": invoice_id,
        "orderId": str(invoice["orderId"]),
        "correlationId": str(invoice["correlationId"]),
        "downloadUrl": f"/invoices/{invoice_id}/pdf" if invoice.get("pdfPath") else None,
    }
