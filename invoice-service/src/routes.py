"""HTTP-Router (Router-Schicht) des invoice-service.

Zwei GET-Endpunkte fuer Metadaten (/invoices/{invoiceId}) und PDF-Download
(/invoices/{invoiceId}/pdf). Reine Weiterleitung an die Repository-Schicht
(database.py) plus Serialisierung (_serialize_invoice() aus service.py).
"""

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
    """Liefert Metadaten (Status, Versuche, Fehler, Download-URL) zu einer Rechnung."""
    invoice = get_invoice_record(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    return InvoiceResponse(**_serialize_invoice(invoice))


@router.get("/invoices/{invoiceId}/pdf")
async def download_invoice_pdf(invoiceId: str) -> FileResponse:
    """Liefert die erzeugte PDF-Datei zum Download aus.

    409 statt 404, wenn die Rechnung existiert, aber (noch) nicht CREATED
    ist - unterscheidet "gibt's nicht" von "gibt's, ist aber noch nicht
    fertig/fehlgeschlagen" fuer den aufrufenden Client.
    """
    invoice = get_invoice_record(invoiceId)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoiceId} not found")
    if invoice.get("status") != "CREATED" or not invoice.get("pdfPath"):
        raise HTTPException(status_code=409, detail=f"Invoice {invoiceId} is not ready for download")
    pdf_path = Path(invoice["pdfPath"])
    if not pdf_path.exists() or not pdf_path.is_file():
        # DB sagt "CREATED", aber die Datei fehlt (z.B. Volume verloren) -
        # 404 statt 500, weil aus Client-Sicht schlicht keine PDF verfuegbar ist.
        raise HTTPException(status_code=404, detail=f"Invoice PDF for {invoiceId} not found")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"retro-parts-invoice-{invoiceId}.pdf",
    )
