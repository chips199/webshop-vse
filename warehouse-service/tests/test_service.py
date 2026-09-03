import unittest
from unittest.mock import patch

from src.service import handle_warehouse_message


def _message(message_type: str, payload: dict) -> dict:
    return {
        "messageId": "msg-1",
        "correlationId": "corr-1",
        "type": message_type,
        "payload": payload,
    }


class HandleWarehouseCancelMessageTest(unittest.TestCase):
    def test_cancel_succeeded_publishes_succeeded_status(self) -> None:
        message = _message("warehouse.cancel.requested", {"orderId": "order-1"})
        with patch("src.service.cancel_reservation", return_value=True) as cancel_reservation, \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        cancel_reservation.assert_called_once_with("order-1")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.cancel.succeeded")
        self.assertEqual(event["payload"]["cancelStatus"], "SUCCEEDED")
        self.assertEqual(event["payload"]["reasonCode"], "CANCEL_REQUESTED")

    def test_cancel_not_found_publishes_skipped_status(self) -> None:
        message = _message("warehouse.cancel.requested", {"orderId": "order-2"})
        with patch("src.service.cancel_reservation", return_value=False), \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["cancelStatus"], "SKIPPED")

    def test_cancel_passes_through_custom_reason_code(self) -> None:
        message = _message(
            "warehouse.cancel.requested", {"orderId": "order-3", "reasonCode": "PAYMENT_CANCELLED"}
        )
        with patch("src.service.cancel_reservation", return_value=True), \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_CANCELLED")


class HandleWarehouseCommitMessageTest(unittest.TestCase):
    def _commit_payload(self, **overrides) -> dict:
        payload = {
            "orderId": "order-1",
            "transactionId": "tx-1",
            "provider": "stripe",
            "amount": "49.90",
            "currency": "EUR",
        }
        payload.update(overrides)
        return payload

    def test_commit_forced_failure_scenario_skips_commit_reservation(self) -> None:
        message = _message(
            "warehouse.commit.requested", self._commit_payload(scenario="warehouse_commit_failed")
        )
        with patch("src.service.commit_reservation") as commit_reservation, \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        commit_reservation.assert_not_called()
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.commit.failed")
        self.assertEqual(event["payload"]["reasonCode"], "WAREHOUSE_COMMIT_FAILED")

    def test_commit_success_publishes_succeeded_status(self) -> None:
        message = _message("warehouse.commit.requested", self._commit_payload())
        with patch("src.service.commit_reservation", return_value=True) as commit_reservation, \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        commit_reservation.assert_called_once_with("order-1")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.commit.succeeded")
        self.assertEqual(event["payload"]["commitStatus"], "SUCCEEDED")

    def test_commit_not_found_publishes_failed_status(self) -> None:
        message = _message("warehouse.commit.requested", self._commit_payload())
        with patch("src.service.commit_reservation", return_value=False), \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.commit.failed")
        self.assertEqual(event["payload"]["reasonCode"], "WAREHOUSE_COMMIT_FAILED")


class HandleWarehouseReserveMessageTest(unittest.TestCase):
    def _reserve_payload(self, **overrides) -> dict:
        payload = {
            "orderId": "order-1",
            "items": [{"productId": "product-1", "quantity": 2}],
            "amount": "49.90",
            "currency": "EUR",
            "provider": "stripe",
        }
        payload.update(overrides)
        return payload

    def test_reserve_success_publishes_reservation_succeeded(self) -> None:
        message = _message("warehouse.reserve.requested", self._reserve_payload())
        with patch("src.service.reserve_stock", return_value=(True, None)) as reserve_stock, \
                patch("src.service.cancel_reservation") as cancel_reservation, \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        reserve_stock.assert_called_once()
        cancel_reservation.assert_not_called()
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.reservation.succeeded")
        self.assertEqual(event["payload"]["reservationId"], "reservation-order-1")

    def test_reserve_out_of_stock_publishes_reservation_failed(self) -> None:
        message = _message("warehouse.reserve.requested", self._reserve_payload())
        with patch("src.service.reserve_stock", return_value=(False, "OUT_OF_STOCK")), \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.reservation.failed")
        self.assertEqual(event["payload"]["reasonCode"], "OUT_OF_STOCK")

    def test_reserve_forced_out_of_stock_scenario_cancels_and_fails(self) -> None:
        # Das Testszenario storniert eine zuvor erfolgreiche Reservierung.
        message = _message("warehouse.reserve.requested", self._reserve_payload(scenario="out_of_stock"))
        with patch("src.service.reserve_stock", return_value=(True, None)), \
                patch("src.service.cancel_reservation") as cancel_reservation, \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        cancel_reservation.assert_called_once_with("order-1")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.reservation.failed")
        self.assertEqual(event["payload"]["reasonCode"], "OUT_OF_STOCK")


class HandleWarehouseMessageIgnoresUnknownTypesTest(unittest.TestCase):
    def test_unrelated_message_type_is_ignored(self) -> None:
        message = _message("order.completed", {"orderId": "order-1"})
        with patch("src.service.reserve_stock") as reserve_stock, \
                patch("src.service.commit_reservation") as commit_reservation, \
                patch("src.service.cancel_reservation") as cancel_reservation, \
                patch("src.service.publish_message") as publish_message:
            handle_warehouse_message(message)

        reserve_stock.assert_not_called()
        commit_reservation.assert_not_called()
        cancel_reservation.assert_not_called()
        publish_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
