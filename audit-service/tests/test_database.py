import unittest
from unittest.mock import MagicMock, patch

from src.database import _status_code_for, insert_snapshot_from_message


class StatusCodeForTest(unittest.TestCase):
    """Deckt die reine Ableitungslogik von _status_code_for() ab - inkl. der
    bewussten Prioritaet ".failed" vor ".cancel."/".refund." (siehe
    test_failed_takes_precedence_over_refund_pattern)."""

    def test_failed_event_maps_to_failure(self) -> None:
        self.assertEqual(_status_code_for("billing.payment.failed"), "FAILURE")

    def test_failed_takes_precedence_over_refund_pattern(self) -> None:
        # "billing.refund.failed" enthaelt sowohl ".failed" als auch ".refund." -
        # die Failure-Pruefung steht in _status_code_for() bewusst zuerst.
        self.assertEqual(_status_code_for("billing.refund.failed"), "FAILURE")

    def test_retry_event_maps_to_retry(self) -> None:
        self.assertEqual(_status_code_for("invoice.retry.scheduled"), "RETRY")

    def test_cancel_requested_maps_to_compensating(self) -> None:
        self.assertEqual(_status_code_for("warehouse.cancel.requested"), "COMPENSATING")

    def test_refund_requested_maps_to_compensating(self) -> None:
        self.assertEqual(_status_code_for("billing.refund.requested"), "COMPENSATING")

    def test_rollback_completed_maps_to_compensated(self) -> None:
        self.assertEqual(_status_code_for("order.rollback.completed"), "COMPENSATED")

    def test_regular_event_maps_to_success(self) -> None:
        self.assertEqual(_status_code_for("warehouse.reservation.succeeded"), "SUCCESS")


class InsertSnapshotFromMessageTest(unittest.TestCase):
    """insert_snapshot_from_message() gegen eine gemockte psycopg-Verbindung -
    prueft, dass die INSERT-Query mit den richtig abgeleiteten Werten
    (eventType, statusCode, actor) aufgerufen wird, ohne eine echte DB zu
    brauchen."""

    def _make_message(self, message_type: str = "warehouse.reservation.succeeded") -> dict:
        return {
            "messageId": "11111111-1111-1111-1111-111111111111",
            "correlationId": "22222222-2222-2222-2222-222222222222",
            "type": message_type,
            "sourceService": "warehouse-service",
            "timestamp": "2026-08-23T10:00:00+00:00",
            "payload": {"orderId": "order-1"},
            "previousEventId": "33333333-3333-3333-3333-333333333333",
        }

    def _mock_cursor(self):
        mock_cursor = MagicMock()
        mock_connection = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        return mock_cursor, mock_connection

    def test_inserts_snapshot_with_derived_event_type_and_status_code(self) -> None:
        message = self._make_message()
        mock_cursor, mock_connection = self._mock_cursor()

        with patch("src.database.psycopg.connect") as mock_connect, \
                patch("src.database.Jsonb", side_effect=lambda value: value):
            mock_connect.return_value.__enter__.return_value = mock_connection
            insert_snapshot_from_message(message)

        query, params = mock_cursor.execute.call_args.args
        self.assertIn("ON CONFLICT (id) DO NOTHING", query)
        self.assertEqual(params[0], message["messageId"])
        self.assertEqual(params[1], message["correlationId"])
        self.assertEqual(params[2], "WAREHOUSE_RESERVATION_SUCCEEDED")
        self.assertEqual(params[3], "warehouse-service")
        self.assertEqual(params[4], message["timestamp"])
        self.assertEqual(params[5], {"orderId": "order-1"})
        self.assertEqual(params[6], message["previousEventId"])
        self.assertEqual(params[7], "warehouse-service")
        self.assertEqual(params[8], "SUCCESS")

    def test_failed_event_gets_failure_status_code(self) -> None:
        message = self._make_message("billing.payment.failed")
        mock_cursor, mock_connection = self._mock_cursor()

        with patch("src.database.psycopg.connect") as mock_connect, \
                patch("src.database.Jsonb", side_effect=lambda value: value):
            mock_connect.return_value.__enter__.return_value = mock_connection
            insert_snapshot_from_message(message)

        params = mock_cursor.execute.call_args.args[1]
        self.assertEqual(params[2], "BILLING_PAYMENT_FAILED")
        self.assertEqual(params[8], "FAILURE")

    def test_missing_previous_event_id_is_passed_as_none(self) -> None:
        message = self._make_message()
        del message["previousEventId"]
        mock_cursor, mock_connection = self._mock_cursor()

        with patch("src.database.psycopg.connect") as mock_connect, \
                patch("src.database.Jsonb", side_effect=lambda value: value):
            mock_connect.return_value.__enter__.return_value = mock_connection
            insert_snapshot_from_message(message)

        params = mock_cursor.execute.call_args.args[1]
        self.assertIsNone(params[6])

    def test_missing_payload_defaults_to_empty_dict(self) -> None:
        message = self._make_message()
        del message["payload"]
        mock_cursor, mock_connection = self._mock_cursor()

        with patch("src.database.psycopg.connect") as mock_connect, \
                patch("src.database.Jsonb", side_effect=lambda value: value):
            mock_connect.return_value.__enter__.return_value = mock_connection
            insert_snapshot_from_message(message)

        params = mock_cursor.execute.call_args.args[1]
        self.assertEqual(params[5], {})


if __name__ == "__main__":
    unittest.main()
