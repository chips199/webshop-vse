"""HTTP-Endpunkte des Invoice-Service."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .config import settings
from .database import get_invoice as get_invoice_record
from .schemas import HealthResponse, InvoiceResponse
from .service import _serialize_invoice

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(service=settings.service_name)


@router.get("/invoices/{invoiceId}", response_model=InvoiceResponse)
async def get_invoice(invoiceId: str) -> InvoiceResponse:
    """Liefert die Metadaten einer Rechnung."""
    invoice = get_invoice_record(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    return InvoiceResponse(**_serialize_invoice(invoice))


@router.get("/invoices/{invoiceId}/pdf")
async def download_invoice_pdf(invoiceId: str) -> FileResponse:
    """Liefert die erzeugte PDF-Datei."""
    invoice = get_invoice_record(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    if invoice.get("status") != "CREATED" or not invoice.get("pdfPath"):
        raise HTTPException(status_code=409, detail=f"Invoice {invoiceId} is not ready for download")
    pdf_path = Path(invoice["pdfPath"])
    if not pdf_path.exists() or not pdf_path.is_file():
        # Fehlende Datei trotz CREATED-Status.
        raise HTTPException(status_code=404, detail=f"Invoice PDF for {invoiceId} not found")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"retro-parts-invoice-{invoiceId}.pdf",
    )
