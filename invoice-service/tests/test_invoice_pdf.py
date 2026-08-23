from pathlib import Path
import tempfile
import unittest

from src import pdf as pdf_module
from src import service


class InvoicePdfTest(unittest.TestCase):
    def test_render_pdf_creates_pdf_document(self) -> None:
        pdf = pdf_module.render_pdf(["RETRO PARTS TERMINAL", "Bestellung: order-1"])

        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"RETRO PARTS TERMINAL", pdf)
        self.assertIn(b"Bestellung: order-1", pdf)
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_pdf_escape_handles_parentheses_and_backslashes(self) -> None:
        escaped = pdf_module._pdf_escape(r"CPU (tested) \\ ok")

        self.assertEqual(escaped, r"CPU \(tested\) \\\\ ok")

    def test_create_invoice_pdf_writes_file(self) -> None:
        payload = {
            "orderId": "11111111-1111-1111-1111-111111111111",
            "transactionId": "tx-1",
            "provider": "stripe",
            "amount": "149.90",
            "currency": "EUR",
            "customer": {
                "firstName": "Ada",
                "lastName": "Lovelace",
                "email": "ada@example.test",
                "phone": "+49 30 123456",
            },
            "shippingAddress": {
                "street": "Retroallee",
                "houseNumber": "8",
                "postalCode": "10115",
                "city": "Berlin",
                "country": "DE",
            },
            "items": [
                {
                    "productId": "22222222-2222-2222-2222-222222222222",
                    "name": "Intel 8086 CPU",
                    "quantity": 2,
                    "unitPrice": "74.95",
                    "lineTotal": "149.90",
                    "currency": "EUR",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            original_invoice_dir = pdf_module.invoice_dir
            pdf_module.invoice_dir = Path(temp_dir)
            try:
                path = pdf_module.create_invoice_pdf(
                    "22222222-2222-2222-2222-222222222222",
                    "33333333-3333-3333-3333-333333333333",
                    payload,
                )
            finally:
                pdf_module.invoice_dir = original_invoice_dir

            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".pdf")
            pdf = path.read_bytes()
            self.assertTrue(pdf.startswith(b"%PDF-1.4"))
            self.assertIn(b"Ada Lovelace", pdf)
            self.assertIn(b"Retroallee 8", pdf)
            self.assertIn(b"Intel 8086 CPU", pdf)
            self.assertIn(b"Gesamtbetrag: 149.90 EUR", pdf)

    def test_serialize_invoice_converts_database_types(self) -> None:
        invoice = {
            "invoiceId": "22222222-2222-2222-2222-222222222222",
            "orderId": "11111111-1111-1111-1111-111111111111",
            "correlationId": "33333333-3333-3333-3333-333333333333",
            "status": "CREATED",
            "pdfPath": "invoices/22222222-2222-2222-2222-222222222222.pdf",
            "attempts": 1,
            "lastError": None,
        }

        serialized = service._serialize_invoice(invoice)

        self.assertEqual(serialized["invoiceId"], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(serialized["downloadUrl"], "/invoices/22222222-2222-2222-2222-222222222222/pdf")


if __name__ == "__main__":
    unittest.main()
