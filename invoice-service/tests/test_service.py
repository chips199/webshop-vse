from pathlib import Path
import unittest
from unittest.mock import patch

from src.service import handle_invoice_message


def _create_requested_message(**payload_overrides) -> dict:
    payload = {
        "orderId": "order-1",
        "transactionId": "tx-1",
        "provider": "stripe",
        "amount": "49.90",
        "currency": "EUR",
    }
    payload.update(payload_overrides)
    return {
        "messageId": "msg-1",
        "correlationId": "corr-1",
        "type": "invoice.create.requested",
        "payload": payload,
    }


class HandleInvoiceMessageHappyPathTest(unittest.TestCase):
    def test_successful_render_publishes_invoice_created(self) -> None:
        message = _create_requested_message()
        pdf_path = Path("/tmp/invoices/dummy.pdf")

        with patch("src.service.upsert_invoice_processing") as upsert, \
                patch("src.service.create_invoice_pdf", return_value=pdf_path) as create_pdf, \
                patch("src.service.mark_invoice_created") as mark_created, \
                patch("src.service.mark_invoice_failed") as mark_failed, \
                patch("src.service.publish_message") as publish_message:
            handle_invoice_message(message)

        invoice_id = upsert.call_args.args[0]
        upsert.assert_called_once_with(invoice_id, "corr-1", message["payload"], 1)
        create_pdf.assert_called_once_with(invoice_id, "corr-1", message["payload"])
        mark_created.assert_called_once_with(invoice_id, str(pdf_path), 1)
        mark_failed.assert_not_called()

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "invoice.created")
        self.assertEqual(event["payload"]["invoiceId"], invoice_id)
        self.assertEqual(event["payload"]["orderId"], "order-1")
        self.assertEqual(event["payload"]["status"], "CREATED")
        self.assertEqual(event["payload"]["pdfPath"], str(pdf_path))
        self.assertEqual(event["payload"]["attempts"], 1)

    def test_attempt_defaults_to_one_when_missing(self) -> None:
        message = _create_requested_message()
        with patch("src.service.upsert_invoice_processing") as upsert, \
                patch("src.service.create_invoice_pdf", return_value=Path("/tmp/x.pdf")), \
                patch("src.service.mark_invoice_created"), \
                patch("src.service.publish_message"):
            handle_invoice_message(message)

        self.assertEqual(upsert.call_args.args[3], 1)

    def test_attempt_is_passed_through_when_present(self) -> None:
        message = _create_requested_message(attempt=2)
        with patch("src.service.upsert_invoice_processing") as upsert, \
                patch("src.service.create_invoice_pdf", return_value=Path("/tmp/x.pdf")), \
                patch("src.service.mark_invoice_created") as mark_created, \
                patch("src.service.publish_message") as publish_message:
            handle_invoice_message(message)

        self.assertEqual(upsert.call_args.args[3], 2)
        invoice_id = upsert.call_args.args[0]
        mark_created.assert_called_once_with(invoice_id, str(Path("/tmp/x.pdf")), 2)
        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["attempts"], 2)


class HandleInvoiceMessageFailurePathTest(unittest.TestCase):
    def test_invoice_failed_scenario_skips_pdf_rendering(self) -> None:
        message = _create_requested_message(scenario="invoice_failed")
        with patch("src.service.upsert_invoice_processing") as upsert, \
                patch("src.service.create_invoice_pdf") as create_pdf, \
                patch("src.service.mark_invoice_failed") as mark_failed, \
                patch("src.service.publish_message") as publish_message:
            handle_invoice_message(message)

        upsert.assert_called_once()
        create_pdf.assert_not_called()
        invoice_id = upsert.call_args.args[0]
        mark_failed.assert_called_once()
        self.assertEqual(mark_failed.call_args.args[0], invoice_id)
        self.assertIn("Fehlerszenario", mark_failed.call_args.args[1])

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "invoice.failed")
        self.assertEqual(event["payload"]["orderId"], "order-1")
        self.assertEqual(event["payload"]["reasonCode"], "INVOICE_RENDER_FAILED")
        self.assertEqual(event["payload"]["scenario"], "invoice_failed")
        self.assertEqual(event["payload"]["attempt"], 1)
        self.assertIn("Fehlerszenario", event["payload"]["lastError"])

    def test_unexpected_pdf_rendering_error_is_reported_as_invoice_failed(self) -> None:
        message = _create_requested_message()
        with patch("src.service.upsert_invoice_processing"), \
                patch("src.service.create_invoice_pdf", side_effect=OSError("disk full")), \
                patch("src.service.mark_invoice_failed") as mark_failed, \
                patch("src.service.publish_message") as publish_message:
            handle_invoice_message(message)

        self.assertEqual(mark_failed.call_args.args[1], "disk full")
        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["lastError"], "disk full")
        self.assertEqual(event["payload"]["reasonCode"], "INVOICE_RENDER_FAILED")
        self.assertEqual(event["payload"]["provider"], "stripe")
        self.assertEqual(event["payload"]["amount"], "49.90")
        self.assertEqual(event["payload"]["currency"], "EUR")


class HandleInvoiceMessageIgnoresUnknownTypesTest(unittest.TestCase):
    def test_unrelated_message_type_is_ignored(self) -> None:
        message = {"messageId": "msg-1", "correlationId": "corr-1", "type": "invoice.created", "payload": {}}
        with patch("src.service.upsert_invoice_processing") as upsert, \
                patch("src.service.create_invoice_pdf") as create_pdf, \
                patch("src.service.publish_message") as publish_message:
            handle_invoice_message(message)

        upsert.assert_not_called()
        create_pdf.assert_not_called()
        publish_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
