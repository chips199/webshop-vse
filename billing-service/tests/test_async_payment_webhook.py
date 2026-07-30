import unittest
from unittest.mock import patch

from src.main import AsyncPaymentWebhookRequest, receive_async_payment_webhook


class AsyncPaymentWebhookTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_webhook_publishes_payment_succeeded_event(self) -> None:
        request = AsyncPaymentWebhookRequest(
            orderId="order-async-1",
            transactionId="paypal-order-async-1",
            provider="paypal",
            amount="49.90",
            currency="EUR",
            status="SUCCEEDED",
            correlationId="corr-async-1",
            previousEventId="event-payment-requested",
        )

        with patch("src.main.publish_message") as publish_message:
            response = await receive_async_payment_webhook(request)

        self.assertTrue(response.accepted)
        self.assertEqual(response.eventType, "billing.payment.succeeded")
        routing_key, message = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.succeeded")
        self.assertEqual(message["correlationId"], "corr-async-1")
        self.assertEqual(message["previousEventId"], "event-payment-requested")
        self.assertEqual(message["payload"]["orderId"], "order-async-1")
        self.assertEqual(message["payload"]["paymentStatus"], "SUCCEEDED")

    async def test_failure_webhook_publishes_payment_failed_event(self) -> None:
        request = AsyncPaymentWebhookRequest(
            orderId="order-async-2",
            transactionId="paypal-order-async-2",
            provider="paypal",
            amount="49.90",
            currency="EUR",
            status="FAILED",
            correlationId="corr-async-2",
            previousEventId="event-payment-requested",
            reasonCode="ASYNC_DECLINED",
            message="Async stub declined the payment.",
        )

        with patch("src.main.publish_message") as publish_message:
            response = await receive_async_payment_webhook(request)

        self.assertEqual(response.eventType, "billing.payment.failed")
        routing_key, message = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.failed")
        self.assertEqual(message["payload"]["orderId"], "order-async-2")
        self.assertEqual(message["payload"]["transactionId"], "paypal-order-async-2")
        self.assertEqual(message["payload"]["reasonCode"], "ASYNC_DECLINED")


if __name__ == "__main__":
    unittest.main()
