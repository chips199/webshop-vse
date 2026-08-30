import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response

from src import routes
from src.schemas import (
    Address,
    AdminLoginRequest,
    CreateOrderRequest,
    Customer,
    OrderItem,
    PaymentConfirmationRequest,
    PaymentSelection,
)
from src.service import _request_hash


class _FakeRequest:
    def __init__(
        self,
        correlation_id: str = "corr-1",
        headers: dict | None = None,
        cookies: dict | None = None,
    ) -> None:
        self.state = SimpleNamespace(correlation_id=correlation_id)
        self.headers = headers or {}
        self.cookies = cookies or {}


class _FakeUpload:
    def __init__(self, filename: str, content_type: str, content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _make_order_request() -> CreateOrderRequest:
    return CreateOrderRequest(
        customerId="customer-1",
        customer=Customer(firstName="Ada", lastName="Lovelace", email="ada@example.test"),
        shippingAddress=Address(
            street="Retroallee",
            houseNumber="8",
            postalCode="10115",
            city="Berlin",
            country="DE",
        ),
        items=[OrderItem(productId="product-1", quantity=2)],
        payment=PaymentSelection(provider="stripe"),
    )


def _payment_order(**overrides) -> dict:
    order = {
        "orderId": "order-1",
        "correlationId": "corr-1",
        "status": "PAYMENT_ACTION_REQUIRED",
        "amount": "49.90",
        "currency": "EUR",
        "transactionId": "tx-1",
        "payment": {"provider": "paypal"},
        "paymentRedirectUrl": "https://pay.example.test/tx-1",
        "customer": {"email": "ada@example.test"},
        "shippingAddress": {"city": "Berlin"},
    }
    order.update(overrides)
    return order


class CreateOrderIdempotencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_key_and_body_returns_existing_order_without_side_effects(self) -> None:
        order = _make_order_request()
        existing = {
            "orderId": "order-existing",
            "correlationId": "corr-existing",
            "status": "COMPLETED",
            "amount": "149.90",
            "currency": "EUR",
            "requestHash": _request_hash(order),
        }
        request = _FakeRequest(headers={"Idempotency-Key": "checkout-1"})
        with patch("src.routes.get_order_by_idempotency_key", return_value=existing), \
                patch("src.routes.create_order_record") as create_record, \
                patch("src.routes.publish_message") as publish_message:
            response = await routes.create_order(request, order)

        self.assertEqual(response.orderId, "order-existing")
        self.assertEqual(response.status, "PENDING")
        create_record.assert_not_called()
        publish_message.assert_not_called()

    async def test_same_key_with_different_body_returns_conflict(self) -> None:
        order = _make_order_request()
        existing = {
            "orderId": "order-existing",
            "correlationId": "corr-existing",
            "amount": "149.90",
            "currency": "EUR",
            "requestHash": "different-request-hash",
        }
        request = _FakeRequest(headers={"Idempotency-Key": "checkout-1"})
        with patch("src.routes.get_order_by_idempotency_key", return_value=existing), \
                patch("src.routes.publish_message") as publish_message:
            with self.assertRaises(HTTPException) as ctx:
                await routes.create_order(request, order)

        self.assertEqual(ctx.exception.status_code, 409)
        publish_message.assert_not_called()


class ConfirmOrderPaymentTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_order_returns_not_found(self) -> None:
        with patch("src.routes.get_order_record", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                await routes.confirm_order_payment(
                    "missing-order", PaymentConfirmationRequest(outcome="approved")
                )

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_order_in_wrong_status_returns_conflict(self) -> None:
        with patch("src.routes.get_order_record", return_value=_payment_order(status="COMPLETED")), \
                patch("src.routes.claim_payment_confirmation") as claim:
            with self.assertRaises(HTTPException) as ctx:
                await routes.confirm_order_payment(
                    "order-1", PaymentConfirmationRequest(outcome="approved")
                )

        self.assertEqual(ctx.exception.status_code, 409)
        claim.assert_not_called()

    async def test_duplicate_confirmation_returns_conflict_without_publishing(self) -> None:
        with patch("src.routes.get_order_record", return_value=_payment_order()), \
                patch("src.routes.claim_payment_confirmation", return_value=False), \
                patch("src.routes.publish_message") as publish_message:
            with self.assertRaises(HTTPException) as ctx:
                await routes.confirm_order_payment(
                    "order-1", PaymentConfirmationRequest(outcome="approved")
                )

        self.assertEqual(ctx.exception.status_code, 409)
        publish_message.assert_not_called()

    async def test_approved_confirmation_requests_payment_capture(self) -> None:
        order = _payment_order()
        with patch("src.routes.get_order_record", side_effect=[order, order]), \
                patch("src.routes.claim_payment_confirmation", return_value=True), \
                patch("src.routes.notify_admin_dashboard"), \
                patch("src.routes.publish_message") as publish_message:
            response = await routes.confirm_order_payment(
                "order-1", PaymentConfirmationRequest(outcome="approved")
            )

        routing_key, event = publish_message.call_args.args
        self.assertEqual(response.orderId, "order-1")
        self.assertEqual(routing_key, "billing.payment.confirm.requested")
        self.assertEqual(event["correlationId"], "corr-1")
        self.assertEqual(event["payload"]["transactionId"], "tx-1")
        self.assertEqual(event["payload"]["provider"], "paypal")

    async def test_cancelled_confirmation_marks_failed_and_cancels_reservation(self) -> None:
        order = _payment_order()
        with patch("src.routes.get_order_record", side_effect=[order, order]), \
                patch("src.routes.claim_payment_confirmation", return_value=True), \
                patch("src.routes.notify_admin_dashboard"), \
                patch("src.routes.update_order_status") as update_status, \
                patch("src.routes.publish_message") as publish_message:
            await routes.confirm_order_payment(
                "order-1", PaymentConfirmationRequest(outcome="cancelled")
            )

        update_status.assert_called_once_with("order-1", "PAYMENT_FAILED")
        routing_key, event = publish_message.call_args.args
        self.assertEqual(routing_key, "warehouse.cancel.requested")
        self.assertEqual(event["payload"]["reasonCode"], "PAYMENT_CANCELLED")


class AdminLoginTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_credentials_return_unauthorized(self) -> None:
        response = Response()
        credentials = AdminLoginRequest(username="admin", password="wrong")
        with patch("src.routes.verify_admin_credentials", return_value=False), \
                patch("src.routes.create_admin_session") as create_session:
            with self.assertRaises(HTTPException) as ctx:
                await routes.admin_login(credentials, response)

        self.assertEqual(ctx.exception.status_code, 401)
        create_session.assert_not_called()

    async def test_valid_credentials_create_session_and_secure_cookie(self) -> None:
        response = Response()
        credentials = AdminLoginRequest(username="admin", password="secret")
        with patch("src.routes.verify_admin_credentials", return_value=True), \
                patch("src.routes.secrets.token_urlsafe", return_value="session-token"), \
                patch("src.routes.create_admin_session") as create_session, \
                patch.object(routes.settings, "admin_cookie_secure", True):
            result = await routes.admin_login(credentials, response)

        self.assertTrue(result.authenticated)
        self.assertEqual(result.username, "admin")
        create_session.assert_called_once()
        self.assertNotIn("session-token", str(create_session.call_args.args[0]))
        cookie = response.headers["set-cookie"]
        self.assertIn("admin_session=session-token", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)


class AdminUploadProductImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unsupported_content_type(self) -> None:
        upload = _FakeUpload("product.svg", "image/svg+xml", b"<svg/>")

        with self.assertRaises(HTTPException) as ctx:
            await routes.admin_upload_product_image(upload)

        self.assertEqual(ctx.exception.status_code, 415)

    async def test_rejects_image_larger_than_six_megabytes(self) -> None:
        upload = _FakeUpload("product.png", "image/png", b"x" * (6 * 1024 * 1024 + 1))

        with self.assertRaises(HTTPException) as ctx:
            await routes.admin_upload_product_image(upload)

        self.assertEqual(ctx.exception.status_code, 413)

    async def test_sanitizes_filename_and_writes_inside_upload_directory(self) -> None:
        upload = _FakeUpload("../../My Product!.PNG", "image/png", b"png-content")
        with tempfile.TemporaryDirectory() as upload_dir, \
                patch.object(routes.settings, "product_image_upload_dir", upload_dir), \
                patch.object(routes.settings, "shop_public_base_url", "https://shop.example.test/"), \
                patch("src.routes.uuid4", return_value=SimpleNamespace(hex="abc123def456")):
            result = await routes.admin_upload_product_image(upload)
            saved_path = Path(upload_dir) / result.filename

            self.assertEqual(result.filename, "my-product-abc123def4.png")
            self.assertTrue(saved_path.is_file())
            self.assertEqual(saved_path.read_bytes(), b"png-content")
            self.assertEqual(
                result.imageUrl,
                "https://shop.example.test/product-images/my-product-abc123def4.png",
            )


if __name__ == "__main__":
    unittest.main()
