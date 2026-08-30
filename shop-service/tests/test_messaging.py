import json
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import messaging


class BuildMessageTest(unittest.TestCase):
    def test_builds_complete_event_envelope(self) -> None:
        payload = {"orderId": "order-1"}

        message = messaging.build_message(
            "order.created", "corr-1", payload, previous_event_id="previous-1"
        )

        self.assertEqual(message["type"], "order.created")
        self.assertEqual(message["correlationId"], "corr-1")
        self.assertEqual(message["sourceService"], messaging.settings.service_name)
        self.assertEqual(message["payload"], payload)
        self.assertEqual(message["previousEventId"], "previous-1")
        self.assertTrue(message["messageId"])
        datetime.fromisoformat(message["timestamp"])


class PublishMessageTest(unittest.TestCase):
    def _connection(self):
        connection = MagicMock()
        connection.channel.return_value = MagicMock()
        return connection

    def test_publishes_persistent_json_message_and_closes_connection(self) -> None:
        connection = self._connection()
        event = {"type": "order.created", "payload": {"orderId": "order-1"}}
        with patch("src.messaging.pika.BlockingConnection", return_value=connection):
            messaging.publish_message("order.created", event)

        channel = connection.channel.return_value
        channel.exchange_declare.assert_called_once_with(
            exchange=messaging.EXCHANGE_NAME,
            exchange_type="topic",
            durable=True,
        )
        publish_kwargs = channel.basic_publish.call_args.kwargs
        self.assertEqual(publish_kwargs["routing_key"], "order.created")
        self.assertEqual(json.loads(publish_kwargs["body"].decode("utf-8")), event)
        self.assertEqual(publish_kwargs["properties"].content_type, "application/json")
        self.assertEqual(publish_kwargs["properties"].delivery_mode, 2)
        connection.close.assert_called_once()

    def test_retries_after_connection_error_and_then_succeeds(self) -> None:
        connection = self._connection()
        with patch(
            "src.messaging.pika.BlockingConnection",
            side_effect=[OSError("offline"), connection],
        ) as connect, patch("src.messaging.time.sleep") as sleep:
            messaging.publish_message("order.created", {"payload": {}})

        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(messaging._PUBLISH_RETRY_BACKOFF_SECONDS)
        connection.channel.return_value.basic_publish.assert_called_once()

    def test_raises_last_error_after_all_publish_attempts_fail(self) -> None:
        with patch(
            "src.messaging.pika.BlockingConnection",
            side_effect=[OSError("first"), OSError("second"), OSError("third")],
        ) as connect, patch("src.messaging.time.sleep") as sleep:
            with self.assertRaisesRegex(OSError, "third"):
                messaging.publish_message("order.created", {"payload": {}})

        self.assertEqual(connect.call_count, messaging._PUBLISH_MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, messaging._PUBLISH_MAX_ATTEMPTS - 1)


class ConsumeMessagesTest(unittest.TestCase):
    def _connection_with_message(self, body: dict):
        method_frame = SimpleNamespace(delivery_tag=42)
        channel = MagicMock()
        channel.consume.return_value = [(method_frame, None, json.dumps(body).encode("utf-8"))]
        connection = MagicMock()
        connection.channel.return_value = channel
        return connection, channel

    def test_acknowledges_successfully_handled_message(self) -> None:
        stop_event = threading.Event()
        connection, channel = self._connection_with_message({"type": "invoice.created"})

        def handle_message(message: dict) -> None:
            self.assertEqual(message["type"], "invoice.created")
            stop_event.set()

        with patch("src.messaging._connect_with_retry", return_value=connection):
            messaging.consume_messages(["invoice.*"], handle_message, stop_event)

        channel.basic_ack.assert_called_once_with(42)
        channel.basic_nack.assert_not_called()
        channel.queue_bind.assert_called_once_with(
            queue=messaging.SHOP_QUEUE_NAME,
            exchange=messaging.EXCHANGE_NAME,
            routing_key="invoice.*",
        )
        channel.cancel.assert_called_once()
        connection.close.assert_called_once()

    def test_rejects_failed_message_without_requeue(self) -> None:
        stop_event = threading.Event()
        connection, channel = self._connection_with_message({"type": "invoice.failed"})

        def handle_message(_message: dict) -> None:
            stop_event.set()
            raise RuntimeError("handler failed")

        with patch("src.messaging._connect_with_retry", return_value=connection):
            messaging.consume_messages(["invoice.*"], handle_message, stop_event)

        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(42, requeue=False)


if __name__ == "__main__":
    unittest.main()
