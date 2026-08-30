import unittest
from unittest.mock import patch

from src import saga
from src.config import settings
from src.resilience import CircuitBreaker, CircuitBreakerOpenError, CircuitState, CircuitTransition


def _message(message_type: str, payload: dict, message_id: str = "msg-1", correlation_id: str = "corr-1") -> dict:
    return {"messageId": message_id, "correlationId": correlation_id, "type": message_type, "payload": payload}


class FreshCircuitBreakerTestCase(unittest.TestCase):
    """Basisklasse fuer Tests, die den Invoice-Circuit-Breaker beruehren -
    ersetzt das Modul-Singleton durch eine frische Instanz, damit Tests sich
    nicht gegenseitig ueber den gemeinsamen Zustand beeinflussen."""

    def setUp(self) -> None:
        self._original_breaker = saga.invoice_circuit_breaker
        saga.invoice_circuit_breaker = CircuitBreaker(
            failure_threshold=settings.invoice_circuit_breaker_failure_threshold,
            reset_seconds=settings.invoice_circuit_breaker_reset_seconds,
            half_open_max_calls=settings.invoice_circuit_breaker_half_open_max_calls,
        )

    def tearDown(self) -> None:
        saga.invoice_circuit_breaker = self._original_breaker


class HandleSagaMessageWarehouseReservationTest(unittest.TestCase):
    def test_reservation_succeeded_requests_payment_and_updates_status(self) -> None:
        message = _message(
            "warehouse.reservation.succeeded",
            {"orderId": "order-1", "amount": "49.90", "currency": "EUR", "provider": "stripe"},
        )
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        update_status.assert_any_call("order-1", "RESERVED")
        update_status.assert_any_call("order-1", "PAYMENT_PENDING")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.payment.requested")
        self.assertEqual(event["payload"]["orderId"], "order-1")

    def test_reservation_failed_marks_order_out_of_stock(self) -> None:
        message = _message("warehouse.reservation.failed", {"orderId": "order-2"})
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-2", "OUT_OF_STOCK")
        publish_message.assert_not_called()


class HandleSagaMessageBillingPaymentPendingTest(unittest.TestCase):
    def test_pending_with_redirect_url_marks_action_required(self) -> None:
        message = _message(
            "billing.payment.pending",
            {"orderId": "order-1", "transactionId": "tx-1", "redirectUrl": "https://pay.test/1"},
        )
        with patch("src.saga.update_payment_action_required") as update_action_required:
            saga.handle_saga_message(message)

        update_action_required.assert_called_once_with("order-1", "tx-1", "https://pay.test/1")

    def test_pending_without_redirect_url_does_nothing(self) -> None:
        message = _message("billing.payment.pending", {"orderId": "order-1"})
        with patch("src.saga.update_payment_action_required") as update_action_required:
            saga.handle_saga_message(message)

        update_action_required.assert_not_called()


class HandleSagaMessageBillingPaymentSucceededTest(FreshCircuitBreakerTestCase):
    def test_succeeded_requests_invoice_and_warehouse_commit(self) -> None:
        message = _message(
            "billing.payment.succeeded",
            {
                "orderId": "order-1",
                "transactionId": "tx-1",
                "provider": "stripe",
                "amount": "49.90",
                "currency": "EUR",
            },
        )
        with patch("src.saga.update_payment_succeeded") as update_payment_succeeded, \
                patch("src.saga.get_order_record", return_value={}), \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        update_payment_succeeded.assert_called_once()
        routing_keys = [call.args[0] for call in publish_message.call_args_list]
        self.assertIn("invoice.create.requested", routing_keys)
        self.assertIn("warehouse.commit.requested", routing_keys)


class HandleSagaMessageBillingPaymentFailedTest(unittest.TestCase):
    def test_failed_cancels_warehouse_reservation(self) -> None:
        message = _message("billing.payment.failed", {"orderId": "order-1", "reasonCode": "PAYMENT_DECLINED"})
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "PAYMENT_FAILED")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.cancel.requested")
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_DECLINED")


class HandleSagaMessageWarehouseCancelSucceededTest(unittest.TestCase):
    def test_cancel_succeeded_marks_payment_failed(self) -> None:
        message = _message("warehouse.cancel.succeeded", {"orderId": "order-1"})
        with patch("src.saga.update_order_status") as update_status:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "PAYMENT_FAILED")


class HandleSagaMessageInvoiceCreatedTest(FreshCircuitBreakerTestCase):
    def test_invoice_created_updates_status_and_checks_completion(self) -> None:
        message = _message("invoice.created", {"orderId": "order-1", "invoiceId": "invoice-1"})
        with patch("src.saga.update_invoice_created") as update_invoice_created, \
                patch("src.saga.complete_order_if_ready", return_value=False) as complete_if_ready:
            saga.handle_saga_message(message)

        update_invoice_created.assert_called_once_with("order-1", "invoice-1")
        complete_if_ready.assert_called_once_with("order-1")

    def test_invoice_created_publishes_order_completed_when_ready(self) -> None:
        message = _message("invoice.created", {"orderId": "order-1", "invoiceId": "invoice-1"})
        with patch("src.saga.update_invoice_created"), \
                patch("src.saga.complete_order_if_ready", return_value=True), \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        routing_keys = [call.args[0] for call in publish_message.call_args_list]
        self.assertIn("order.completed", routing_keys)


class HandleSagaMessageInvoiceFailedTest(FreshCircuitBreakerTestCase):
    def test_retries_when_attempts_remain(self) -> None:
        message = _message(
            "invoice.failed", {"orderId": "order-1", "attempt": 1, "reasonCode": "INVOICE_RENDER_FAILED"}
        )
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.schedule_invoice_retry") as schedule_retry:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "INVOICE_RETRY_PENDING")
        schedule_retry.assert_called_once()
        self.assertEqual(schedule_retry.call_args.args[4], 1)

    def test_marks_permanently_failed_after_exhausting_retries(self) -> None:
        max_attempts = settings.invoice_max_retries
        message = _message(
            "invoice.failed",
            {"orderId": "order-1", "attempt": max_attempts, "reasonCode": "INVOICE_RENDER_FAILED"},
        )
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.schedule_invoice_retry") as schedule_retry:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "INVOICE_FAILED")
        schedule_retry.assert_not_called()

    def test_defaults_attempt_to_one_when_missing(self) -> None:
        message = _message("invoice.failed", {"orderId": "order-1", "reasonCode": "INVOICE_RENDER_FAILED"})
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.schedule_invoice_retry"):
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "INVOICE_RETRY_PENDING")


class HandleSagaMessageWarehouseCommitTest(FreshCircuitBreakerTestCase):
    def test_commit_succeeded_checks_completion(self) -> None:
        message = _message("warehouse.commit.succeeded", {"orderId": "order-1"})
        with patch("src.saga.update_warehouse_commit") as update_commit, \
                patch("src.saga.complete_order_if_ready", return_value=False) as complete_if_ready:
            saga.handle_saga_message(message)

        update_commit.assert_called_once_with("order-1", "SUCCEEDED")
        complete_if_ready.assert_called_once_with("order-1")

    def test_commit_failed_requests_refund(self) -> None:
        message = _message(
            "warehouse.commit.failed",
            {
                "orderId": "order-1",
                "transactionId": "tx-1",
                "provider": "stripe",
                "amount": "49.90",
                "currency": "EUR",
            },
        )
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "REFUND_PENDING")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "billing.refund.requested")
        self.assertEqual(event["payload"]["transactionId"], "tx-1")


class HandleSagaMessageBillingRefundTest(unittest.TestCase):
    def test_refund_succeeded_completes_rollback(self) -> None:
        message = _message(
            "billing.refund.succeeded", {"orderId": "order-1", "transactionId": "tx-1"}
        )
        with patch("src.saga.update_order_status") as update_status, \
                patch("src.saga.publish_message") as publish_message:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "ROLLBACK_COMPLETED")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "order.rollback.completed")

    def test_refund_failed_marks_order(self) -> None:
        message = _message("billing.refund.failed", {"orderId": "order-1"})
        with patch("src.saga.update_order_status") as update_status:
            saga.handle_saga_message(message)

        update_status.assert_called_once_with("order-1", "REFUND_FAILED")


class RequestInvoiceWithCircuitTest(FreshCircuitBreakerTestCase):
    def test_publishes_invoice_create_requested_when_circuit_closed(self) -> None:
        with patch("src.saga.get_order_record", return_value={"items": [], "customer": {}}), \
                patch("src.saga.publish_message") as publish_message:
            saga.request_invoice_with_circuit(
                "order-1", "corr-1", {"transactionId": "tx-1", "provider": "stripe", "amount": "49.90", "currency": "EUR"},
                {"messageId": "msg-1"},
            )

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "invoice.create.requested")
        self.assertEqual(event["payload"]["attempt"], 1)

    def test_invoice_request_contains_order_details_and_retry_attempt(self) -> None:
        order = {
            "customer": {"email": "ada@example.test"},
            "shippingAddress": {"city": "Berlin"},
            "billingAddress": {"city": "London"},
            "items": [{"productId": "product-1", "quantity": 2}],
        }
        payload = {
            "transactionId": "tx-1",
            "provider": "stripe",
            "amount": "49.90",
            "currency": "EUR",
            "scenario": "invoice_failed",
        }
        with patch("src.saga.get_order_record", return_value=order), \
                patch("src.saga.publish_message") as publish_message:
            saga.request_invoice_with_circuit(
                "order-1", "corr-1", payload, {"messageId": "msg-1"}, attempt=2
            )

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "invoice.create.requested")
        self.assertEqual(event["payload"]["customer"], order["customer"])
        self.assertEqual(event["payload"]["shippingAddress"], order["shippingAddress"])
        self.assertEqual(event["payload"]["billingAddress"], order["billingAddress"])
        self.assertEqual(event["payload"]["items"], order["items"])
        self.assertEqual(event["payload"]["attempt"], 2)
        self.assertEqual(event["previousEventId"], "msg-1")

    def test_blocked_by_open_circuit_marks_invoice_failed_without_publishing(self) -> None:
        with patch.object(saga.invoice_circuit_breaker, "before_call", side_effect=CircuitBreakerOpenError("open")):
            with patch("src.saga.update_order_status") as update_status, \
                    patch("src.saga.publish_message") as publish_message:
                saga.request_invoice_with_circuit(
                    "order-1", "corr-1", {"transactionId": "tx-1", "provider": "stripe", "amount": "49.90", "currency": "EUR"},
                    {"messageId": "msg-1"},
                )

        update_status.assert_called_once_with("order-1", "INVOICE_FAILED")
        publish_message.assert_not_called()


class ScheduleInvoiceRetryTest(unittest.TestCase):
    def test_publishes_retry_scheduled_event_and_starts_timer(self) -> None:
        with patch("src.saga.publish_message") as publish_message, \
                patch("src.saga.threading.Timer") as timer_cls:
            saga.schedule_invoice_retry(
                "order-1", "corr-1",
                {"transactionId": "tx-1", "reasonCode": "INVOICE_RENDER_FAILED"},
                {"messageId": "msg-1"},
                1,
            )

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "invoice.retry.scheduled")
        self.assertEqual(event["payload"]["attempt"], 2)
        timer_cls.assert_called_once()
        self.assertEqual(timer_cls.call_args.args[0], settings.invoice_retry_backoff_seconds)
        self.assertIs(timer_cls.call_args.args[1], saga.request_invoice_with_circuit)
        self.assertEqual(timer_cls.call_args.kwargs["kwargs"]["attempt"], 2)
        self.assertEqual(timer_cls.call_args.kwargs["kwargs"]["previous_message"], event)
        self.assertTrue(timer_cls.return_value.daemon)
        timer_cls.return_value.start.assert_called_once()


class PublishInvoiceCircuitTransitionTest(unittest.TestCase):
    def test_none_transition_does_not_publish(self) -> None:
        with patch("src.saga.publish_message") as publish_message:
            saga.publish_invoice_circuit_transition("corr-1", "order-1", None, "msg-1")

        publish_message.assert_not_called()

    def test_transition_is_published_with_complete_state_payload(self) -> None:
        transition = CircuitTransition(
            previous_state=CircuitState.CLOSED,
            state=CircuitState.OPEN,
            failure_count=3,
            reason="INVOICE_RENDER_FAILED",
        )
        with patch("src.saga.publish_message") as publish_message:
            saga.publish_invoice_circuit_transition("corr-1", "order-1", transition, "msg-1")

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "invoice.circuit.state.changed")
        self.assertEqual(event["previousEventId"], "msg-1")
        self.assertEqual(
            event["payload"],
            {
                "circuitName": "invoice-service",
                "orderId": "order-1",
                "previousState": "CLOSED",
                "state": "OPEN",
                "failureCount": 3,
                "reasonCode": "INVOICE_RENDER_FAILED",
            },
        )


class NotifyAdminDashboardTest(unittest.TestCase):
    def test_missing_order_id_does_not_publish(self) -> None:
        with patch("src.saga.realtime.publish") as realtime_publish:
            saga.notify_admin_dashboard(None, "corr-1", "invoice.failed")

        realtime_publish.assert_not_called()

    def test_realtime_failure_is_swallowed(self) -> None:
        with patch("src.saga.realtime.publish", side_effect=RuntimeError("offline")):
            saga.notify_admin_dashboard("order-1", "corr-1", "invoice.failed")


class MaybePublishOrderCompletedTest(unittest.TestCase):
    def test_does_not_publish_when_not_ready(self) -> None:
        with patch("src.saga.complete_order_if_ready", return_value=False), \
                patch("src.saga.publish_message") as publish_message:
            saga.maybe_publish_order_completed("order-1", "corr-1", {"messageId": "msg-1"})

        publish_message.assert_not_called()

    def test_publishes_order_completed_when_ready(self) -> None:
        with patch("src.saga.complete_order_if_ready", return_value=True), \
                patch("src.saga.publish_message") as publish_message:
            saga.maybe_publish_order_completed("order-1", "corr-1", {"messageId": "msg-1"})

        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "order.completed")
        self.assertEqual(event["payload"]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
