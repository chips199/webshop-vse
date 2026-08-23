"""PDF-Rendering des invoice-service.

Baut aus einem Rechnungs-Payload zunaechst eine reine Textzeilenliste
(build_invoice_lines()) und erzeugt daraus dann ein minimalistisches,
gueltiges PDF-Dokument OHNE externe PDF-Bibliothek (render_pdf()) - direkt
als PDF-1.4-Byte-Stream (Objekte, Content-Stream, xref-Tabelle, Trailer).
Bewusst kein reportlab/fpdf o.ae. als Abhaengigkeit, da fuer den Zweck
(einfache, monospaced Textrechnung) das PDF-Rohformat selbst mit
ueberschaubarem Aufwand direkt erzeugt werden kann.
"""

from pathlib import Path

from .config import settings

invoice_dir = Path(settings.invoice_output_dir)


def create_invoice_pdf(invoice_id: str, correlation_id: str, payload: dict) -> Path:
    """Rendert die Rechnungszeilen und schreibt sie als PDF-Datei auf Disk.

    mkdir(exist_ok=True) statt einmaligem Setup: robust, falls das
    Ausgabeverzeichnis (z.B. ein frisch gemountetes Docker-Volume) beim
    ersten Aufruf noch nicht existiert.
    """
    invoice_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = invoice_dir / f"{invoice_id}.pdf"
    invoice_path.write_bytes(render_pdf(build_invoice_lines(invoice_id, correlation_id, payload)))
    return invoice_path


def build_invoice_lines(invoice_id: str, correlation_id: str, payload: dict) -> list[str]:
    """Baut den Textinhalt der Rechnung als Liste einzelner Zeilen auf.

    Diese Zeilenliste ist die einzige Schnittstelle zu render_pdf() - so
    bleibt das eigentliche PDF-Erzeugen (Low-Level-Bytes, siehe unten) vom
    fachlichen Rechnungsinhalt getrennt.
    """
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
    """Baut die Kundenzeile ("Vorname Nachname") - "-" als Platzhalter, falls
    keine Namensdaten vorhanden sind (z.B. unvollstaendiges Test-Payload)."""
    name = " ".join(
        value for value in [customer.get("firstName", "").strip(), customer.get("lastName", "").strip()] if value
    )
    return name or "-"


def _format_address(address: dict) -> list[str]:
    """Formatiert eine Adresse als bis zu drei Zeilen (Strasse, PLZ/Ort, Land) -
    leere Teile werden weggelassen statt als leere Zeile angezeigt."""
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
    """Formatiert eine Bestellposition als rechtsbuendige Tabellenzeile
    (Menge/Artikel/Einzelpreis/Summe) fuer die feste Breite der PDF-Seite."""
    quantity = int(item.get("quantity", 0))
    name = str(item.get("name") or item.get("productId") or "Artikel")
    unit_price = _money(item.get("unitPrice", "0.00"), item.get("currency") or fallback_currency)
    line_total = _money(item.get("lineTotal", "0.00"), item.get("currency") or fallback_currency)
    return f"{quantity:>5}  {_truncate(name, 42):<42}  {unit_price:>10}  {line_total:>10}"


def _money(amount, currency: str) -> str:
    """Formatiert einen Geldbetrag als "Betrag Waehrung" (z.B. "49.90 EUR")."""
    return f"{amount} {currency}"


def _truncate(value: str, max_length: int) -> str:
    """Kuerzt lange Artikelnamen mit "..." ab, damit die Tabellenspalten
    im PDF (feste Zeichenbreite, siehe _format_invoice_item) nicht umbrechen."""
    return value if len(value) <= max_length else f"{value[: max_length - 3]}..."


def render_pdf(lines: list[str]) -> bytes:
    """Erzeugt ein minimalistisches, gueltiges PDF-Dokument ohne externe
    PDF-Bibliothek - direkt als PDF-1.4-Byte-Stream (Objekte, Content-Stream,
    xref-Tabelle, Trailer). Bewusst kein reportlab/fpdf o.ae. als Abhaengigkeit,
    da fuer den Zweck (einfache, monospaced Textrechnung) das PDF-Rohformat
    selbst mit ueberschaubarem Aufwand direkt erzeugt werden kann.
    """
    escaped_lines = [_pdf_escape(line) for line in lines]
    # Roher PDF-Content-Stream in PDF-Operator-Syntax (kein Text-Markup):
    # "x y z rg/RG" setzt Fuell-/Linienfarbe (RGB 0..1), "re"/"f"/"S"
    # zeichnen ein Rechteck (Hintergrund + gruener Rahmen im Retro-Look),
    # "BT ... ET" umschliesst einen Textblock, "/F1 <groesse> Tf" waehlt
    # Schriftart/-groesse, "<x> <y> Td" setzt die Cursor-Position, "(...) Tj"
    # zeichnet den Text selbst.
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
        # Kein Seitenumbruch implementiert: bei zu vielen Zeilen (sehr lange
        # Bestellung) werden die untersten Rechnungszeilen schlicht
        # abgeschnitten (y < 70 = unterer Rand erreicht), statt eine zweite
        # PDF-Seite zu erzeugen. Fuer den Umfang des Uebungsprojekts
        # akzeptiert, waere fuer echten Produktivbetrieb aber ein Luecke.
        if y < 70:
            break
        font_size = 15 if index < 2 else 10  # Titelzeilen (Index 0/1) groesser als der Rest
        content_lines.append(f"BT /F1 {font_size} Tf 54 {y} Td ({line}) Tj ET")
        y -= 18 if line else 12  # Leerzeilen etwas kompakter als Textzeilen
    content_lines.extend(
        [
            "0.44 0.94 0.36 rg",
            "BT /F1 10 Tf 54 82 Td (Automatisch erzeugt durch den Invoice-Service.) Tj ET",
        ]
    )
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")
    # Die vier festen PDF-Objekte (Catalog/Pages/Page/Font) plus der
    # eigentliche Content-Stream als fuenftes Objekt - minimale, aber gemaess
    # PDF-Spezifikation gueltige Objekt-Struktur fuer ein Ein-Seiten-Dokument.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    # %PDF-1.4 + 4 nicht-ASCII-Bytes als Kommentar: von der PDF-Spezifikation
    # empfohlener "Binary Marker", damit Tools/Viewer die Datei sofort als
    # Binaerformat (nicht Text) erkennen.
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    # offsets merkt sich die Byte-Position jedes Objekts in der Datei - wird
    # unten fuer die xref-Tabelle gebraucht, ueber die PDF-Viewer beliebige
    # Objekte ohne die Datei komplett einzulesen direkt anspringen koennen.
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    # xref-Tabelle: Objekt 0 ist laut Spezifikation immer der feste
    # "free list head"-Eintrag ("0000000000 65535 f"), danach ein
    # 20-Byte-Eintrag pro echtem Objekt mit dessen Byte-Offset ("n" = in use).
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    # Trailer verweist auf den Catalog (Objekt 1) als Wurzel des Dokuments
    # und auf den Byte-Offset der xref-Tabelle selbst (startxref) - das ist
    # der Einstiegspunkt, den PDF-Viewer beim Oeffnen zuerst lesen.
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _pdf_escape(value: str) -> str:
    """Macht einen Text fuer die PDF-Content-Stream-Syntax "(...)" sicher.

    latin-1 statt UTF-8: das simple PDF-Textformat hier kennt keine
    Zeichensatz-/Encoding-Deklaration und geht von Latin-1 aus - nicht
    darstellbare Zeichen (z.B. Emojis) werden durch "?" ersetzt statt einen
    Fehler zu werfen. Rueckstriche und Klammern muessen zusaetzlich escaped
    werden, weil sie in der "(...) Tj"-Syntax eine syntaktische Bedeutung
    haben (Klammern koennten sonst den Text vorzeitig beenden).
    """
    normalized = value.encode("latin-1", errors="replace").decode("latin-1")
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
