"""Erzeugung einfacher PDF-Rechnungen ohne externe Bibliothek."""

from pathlib import Path

from .config import settings

invoice_dir = Path(settings.invoice_output_dir)


def create_invoice_pdf(invoice_id: str, correlation_id: str, payload: dict) -> Path:
    """Rendert und speichert eine Rechnung."""
    invoice_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = invoice_dir / f"{invoice_id}.pdf"
    invoice_path.write_bytes(render_pdf(build_invoice_lines(invoice_id, correlation_id, payload)))
    return invoice_path


def build_invoice_lines(invoice_id: str, correlation_id: str, payload: dict) -> list[str]:
    """Erzeugt die Textzeilen einer Rechnung."""
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
    """Formatiert den Kundennamen."""
    name = " ".join(
        value for value in [customer.get("firstName", "").strip(), customer.get("lastName", "").strip()] if value
    )
    return name or "-"


def _format_address(address: dict) -> list[str]:
    """Formatiert eine Adresse in bis zu drei Zeilen."""
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
    """Formatiert eine Bestellposition als Tabellenzeile."""
    quantity = int(item.get("quantity", 0))
    name = str(item.get("name") or item.get("productId") or "Artikel")
    unit_price = _money(item.get("unitPrice", "0.00"), item.get("currency") or fallback_currency)
    line_total = _money(item.get("lineTotal", "0.00"), item.get("currency") or fallback_currency)
    return f"{quantity:>5}  {_truncate(name, 42):<42}  {unit_price:>10}  {line_total:>10}"


def _money(amount, currency: str) -> str:
    """Formatiert Betrag und Waehrung."""
    return f"{amount} {currency}"


def _truncate(value: str, max_length: int) -> str:
    """Kuerzt Text auf die angegebene Laenge."""
    return value if len(value) <= max_length else f"{value[: max_length - 3]}..."


def render_pdf(lines: list[str]) -> bytes:
    """Erzeugt einen PDF-1.4-Byte-Stream."""
    escaped_lines = [_pdf_escape(line) for line in lines]
    # PDF-Operatoren fuer Hintergrund, Rahmen und Text.
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
        # Ueberlauf am unteren Seitenrand abschneiden.
        if y < 70:
            break
        font_size = 15 if index < 2 else 10  # Titelzeilen
        content_lines.append(f"BT /F1 {font_size} Tf 54 {y} Td ({line}) Tj ET")
        y -= 18 if line else 12  # Kompakter Abstand fuer Leerzeilen
    content_lines.extend(
        [
            "0.44 0.94 0.36 rg",
            "BT /F1 10 Tf 54 82 Td (Automatisch erzeugt durch den Invoice-Service.) Tj ET",
        ]
    )
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")
    # Catalog, Seitenbaum, Seite, Schrift und Content-Stream.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    # PDF-Kennung mit Binary Marker.
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    # Bytepositionen fuer die xref-Tabelle.
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    # Objekt 0 ist der feste freie Eintrag der xref-Tabelle.
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    # Trailer mit Catalog und Position der xref-Tabelle.
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _pdf_escape(value: str) -> str:
    """Maskiert Text fuer einen PDF-Content-Stream in Latin-1."""
    normalized = value.encode("latin-1", errors="replace").decode("latin-1")
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
