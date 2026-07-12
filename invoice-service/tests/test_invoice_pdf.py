from pathlib import Path
import tempfile
import unittest

from src import main


class InvoicePdfTest(unittest.TestCase):
    def test_render_pdf_creates_pdf_document(self) -> None:
        pdf = main.render_pdf(["RETRO PARTS TERMINAL", "Bestellung: order-1"])

        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"RETRO PARTS TERMINAL", pdf)
        self.assertIn(b"Bestellung: order-1", pdf)
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_pdf_escape_handles_parentheses_and_backslashes(self) -> None:
        escaped = main._pdf_escape(r"CPU (tested) \\ ok")

        self.assertEqual(escaped, r"CPU \(tested\) \\\\ ok")

    def test_create_invoice_pdf_writes_file(self) -> None:
        payload = {
            "orderId": "11111111-1111-1111-1111-111111111111",
            "transactionId": "tx-1",
            "provider": "stripe",
            "amount": "149.90",
            "currency": "EUR",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            original_invoice_dir = main.invoice_dir
            main.invoice_dir = Path(temp_dir)
            try:
                path = main.create_invoice_pdf(
                    "22222222-2222-2222-2222-222222222222",
                    "33333333-3333-3333-3333-333333333333",
                    payload,
                )
            finally:
                main.invoice_dir = original_invoice_dir

            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".pdf")
            self.assertTrue(path.read_bytes().startswith(b"%PDF-1.4"))


if __name__ == "__main__":
    unittest.main()
