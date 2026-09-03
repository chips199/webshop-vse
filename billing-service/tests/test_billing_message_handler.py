import unittest
from unittest.mock import MagicMock, patch

from src.payment import PaymentFacadeError
from src.payment.models import PaymentResult, PaymentStatus
from src.service import handle_billing_message


def _message(message_type: str, payload: dict, message_id: str = "msg-1", correlation_id: str = "corr-1") -> dict:
    return {"messageId": message_id, "correlationId": correlation_id, "type": message_type, "payload": payload}


class HandleBillingPaymentRequestedTest(unittest.TestCase):
    """Tests fuer billing.payment.requested."""

    def _payload(self, **overrides) -> dict:
        payload = {
            "orderId": "order-1",
            "provider": "stripe",
            "amount": "49.90",
            "currency": "EUR",
        }
        payload.update(overrides)
        return payload

    def test_scenario_payment_failed_short_circuits_without_calling_facade(self) -> None:
        message = _message("billing.payment.requested", self._payload(scenario="payment_failed"))
        with patch("src.service.get_payment_facade") as get_facade, \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        get_facade.assert_not_called()
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.failed")
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_DECLINED")

    def test_scenario_payment_timeout_uses_timeout_reason_code(self) -> None:
        message = _message("billing.payment.requested", self._payload(scenario="payment_timeout"))
        with patch("src.service.get_payment_facade") as get_facade, \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        get_facade.assert_not_called()
        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_TIMEOUT")

    def test_successful_charge_publishes_payment_succeeded(self) -> None:
        message = _message("billing.payment.requested", self._payload())
        facade = MagicMock()
        facade.charge.return_value = PaymentResult("tx-1", "stripe", PaymentStatus.SUCCEEDED)
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.succeeded")
        self.assertEqual(event["payload"]["orderId"], "order-1")
        self.assertEqual(event["payload"]["transactionId"], "tx-1")

    def test_pending_charge_publishes_payment_pending_with_redirect(self) -> None:
        message = _message("billing.payment.requested", self._payload())
        facade = MagicMock()
        facade.charge.return_value = PaymentResult(
            "tx-2", "stripe", PaymentStatus.PENDING, redirect_url="https://checkout.stripe.test/session/1"
        )
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.pending")
        self.assertEqual(event["payload"]["redirectUrl"], "https://checkout.stripe.test/session/1")

    def test_declined_charge_publishes_payment_failed(self) -> None:
        message = _message("billing.payment.requested", self._payload())
        facade = MagicMock()
        facade.charge.return_value = PaymentResult(
            "tx-3", "stripe", PaymentStatus.FAILED, reason="Card declined"
        )
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.failed")
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_DECLINED")
        self.assertEqual(event["payload"]["message"], "Card declined")

    def test_facade_error_publishes_provider_error(self) -> None:
        message = _message("billing.payment.requested", self._payload())
        facade = MagicMock()
        facade.charge.side_effect = PaymentFacadeError("timeout after retries")
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.failed")
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_PROVIDER_ERROR")


class HandleBillingPaymentConfirmRequestedTest(unittest.TestCase):
    """Tests fuer billing.payment.confirm.requested."""

    def _payload(self, **overrides) -> dict:
        payload = {
            "orderId": "order-1",
            "transactionId": "tx-1",
            "provider": "paypal",
            "amount": "49.90",
            "currency": "EUR",
        }
        payload.update(overrides)
        return payload

    def test_confirmed_payment_publishes_payment_succeeded_with_customer_data(self) -> None:
        message = _message("billing.payment.confirm.requested", self._payload())
        facade = MagicMock()
        facade.get_status.return_value = PaymentResult(
            "tx-1", "paypal", PaymentStatus.SUCCEEDED,
            customer={"email": "grace@example.test"},
            shipping_address={"city": "Berlin"},
        )
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        facade.get_status.assert_called_once_with("tx-1", correlation_id="corr-1")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.succeeded")
        self.assertEqual(event["payload"]["customer"]["email"], "grace@example.test")
        self.assertEqual(event["payload"]["shippingAddress"]["city"], "Berlin")

    def test_declined_confirmation_publishes_payment_failed(self) -> None:
        message = _message("billing.payment.confirm.requested", self._payload())
        facade = MagicMock()
        facade.get_status.return_value = PaymentResult(
            "tx-1", "paypal", PaymentStatus.FAILED, reason="Buyer voided the order"
        )
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_DECLINED")
        self.assertEqual(event["payload"]["message"], "Buyer voided the order")

    def test_facade_error_publishes_provider_error(self) -> None:
        message = _message("billing.payment.confirm.requested", self._payload())
        facade = MagicMock()
        facade.get_status.side_effect = PaymentFacadeError("PayPal sandbox unreachable")
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        event = publish_message.call_args.args[1]
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_PROVIDER_ERROR")


class HandleBillingRefundRequestedTest(unittest.TestCase):
    def _payload(self, **overrides) -> dict:
        payload = {
            "orderId": "order-1",
            "transactionId": "tx-1",
            "provider": "stripe",
            "amount": "49.90",
            "currency": "EUR",
        }
        payload.update(overrides)
        return payload

    def test_successful_refund_publishes_refund_succeeded(self) -> None:
        message = _message("billing.refund.requested", self._payload())
        facade = MagicMock()
        facade.refund.return_value = PaymentResult("tx-1", "stripe", PaymentStatus.REFUNDED)
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.refund.succeeded")
        self.assertEqual(event["payload"]["refundStatus"], "REFUNDED")

    def test_failed_refund_publishes_refund_failed(self) -> None:
        message = _message("billing.refund.requested", self._payload())
        facade = MagicMock()
        facade.refund.side_effect = PaymentFacadeError("Refund API unavailable")
        with patch("src.service.get_payment_facade", return_value=facade), \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.refund.failed")
        self.assertEqual(event["payload"]["reasonCode"], "REFUND_PROVIDER_ERROR")


class HandleBillingMessageIgnoresUnknownTypesTest(unittest.TestCase):
    def test_unrelated_message_type_is_ignored(self) -> None:
        message = _message("order.created", {"orderId": "order-1"})
        with patch("src.service.get_payment_facade") as get_facade, \
                patch("src.service.publish_message") as publish_message:
            handle_billing_message(message)

        get_facade.assert_not_called()
        publish_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
