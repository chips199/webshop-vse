import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.schemas import Address, CreateOrderRequest, Customer, OrderItem, PaymentSelection
from src.service import (
    ADMIN_SESSION_COOKIE,
    _idempotency_key_from_request,
    _initial_order_response,
    _order_response,
    _request_hash,
    _serialize_order,
    _serialize_product,
    _serialize_snapshot,
    _token_hash,
    require_admin,
)


class _FakeRequest:
    """Minimaler Request-Ersatz mit Headern und Cookies."""

    def __init__(self, headers: dict | None = None, cookies: dict | None = None) -> None:
        self.headers = headers or {}
        self.cookies = cookies or {}


def _make_order_request() -> CreateOrderRequest:
    return CreateOrderRequest(
        customer=Customer(firstName="Ada", lastName="Lovelace", email="ada@example.test"),
        shippingAddress=Address(street="Retroallee", houseNumber="8", postalCode="10115", city="Berlin", country="DE"),
        items=[OrderItem(productId="product-1", quantity=2)],
        payment=PaymentSelection(provider="stripe"),
    )


class IdempotencyKeyFromRequestTest(unittest.TestCase):
    def test_missing_header_returns_none(self) -> None:
        request = _FakeRequest()
        self.assertIsNone(_idempotency_key_from_request(request))

    def test_blank_header_raises_400(self) -> None:
        request = _FakeRequest(headers={"Idempotency-Key": "   "})
        with self.assertRaises(HTTPException) as ctx:
            _idempotency_key_from_request(request)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_too_long_header_raises_400(self) -> None:
        request = _FakeRequest(headers={"Idempotency-Key": "a" * 129})
        with self.assertRaises(HTTPException) as ctx:
            _idempotency_key_from_request(request)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_valid_header_is_stripped_and_returned(self) -> None:
        request = _FakeRequest(headers={"Idempotency-Key": "  key-123  "})
        self.assertEqual(_idempotency_key_from_request(request), "key-123")


class RequestHashTest(unittest.TestCase):
    def test_hash_is_stable_for_identical_request(self) -> None:
        order = _make_order_request()
        self.assertEqual(_request_hash(order), _request_hash(order))

    def test_hash_differs_for_different_request_content(self) -> None:
        order_a = _make_order_request()
        order_b = _make_order_request()
        order_b.items[0].quantity = 5
        self.assertNotEqual(_request_hash(order_a), _request_hash(order_b))


class OrderResponseSerializationTest(unittest.TestCase):
    def _db_order(self, **overrides) -> dict:
        order = {
            "orderId": "order-1",
            "correlationId": "corr-1",
            "status": "PAYMENT_PENDING",
            "amount": "49.90",
            "currency": "EUR",
            "transactionId": "tx-1",
            "paymentRedirectUrl": None,
            "customer": {"email": "ada@example.test"},
            "shippingAddress": {"city": "Berlin"},
        }
        order.update(overrides)
        return order

    def test_order_response_maps_all_fields(self) -> None:
        response = _order_response(self._db_order())
        self.assertEqual(response.orderId, "order-1")
        self.assertEqual(response.status, "PAYMENT_PENDING")
        self.assertEqual(response.transactionId, "tx-1")
        self.assertEqual(response.customer, {"email": "ada@example.test"})

    def test_initial_order_response_always_reports_pending(self) -> None:
        response = _initial_order_response(self._db_order(status="COMPLETED"))
        self.assertEqual(response.status, "PENDING")
        self.assertEqual(response.orderId, "order-1")
        self.assertEqual(response.amount, "49.90")


class SerializeProductTest(unittest.TestCase):
    def _product(self) -> dict:
        return {
            "id": "product-1",
            "name": "Intel 8086 CPU",
            "year": "1978",
            "description": "Historische CPU",
            "price": "74.95",
            "currency": "EUR",
            "imageUrl": "https://example.test/cpu.png",
            "imageAlt": "CPU",
            "imageSource": "",
            "imageLicense": "",
            "imageCredit": "",
        }

    def test_without_stock_data_status_is_unknown(self) -> None:
        serialized = _serialize_product(self._product(), stock=None)
        self.assertEqual(serialized["stockStatus"], "UNKNOWN")

    def test_with_available_stock_status_is_available(self) -> None:
        stock = {"quantityOnHand": 5, "reservedQuantity": 2, "availableQuantity": 3, "location": "CPU-A1"}
        serialized = _serialize_product(self._product(), stock=stock)
        self.assertEqual(serialized["stockStatus"], "AVAILABLE")
        self.assertEqual(serialized["availableQuantity"], 3)

    def test_with_zero_available_stock_status_is_out_of_stock(self) -> None:
        stock = {"quantityOnHand": 2, "reservedQuantity": 2, "availableQuantity": 0, "location": "CPU-A1"}
        serialized = _serialize_product(self._product(), stock=stock)
        self.assertEqual(serialized["stockStatus"], "OUT_OF_STOCK")

    def test_id_and_price_are_stringified(self) -> None:
        serialized = _serialize_product(self._product(), stock=None)
        self.assertIsInstance(serialized["id"], str)
        self.assertIsInstance(serialized["price"], str)


class SerializeOrderTest(unittest.TestCase):
    def test_stringifies_uuid_like_fields(self) -> None:
        order = {
            "orderId": "order-1",
            "correlationId": "corr-1",
            "amount": "49.90",
            "invoiceId": "invoice-1",
        }
        serialized = _serialize_order(order)
        self.assertEqual(serialized["orderId"], "order-1")
        self.assertEqual(serialized["invoiceId"], "invoice-1")

    def test_missing_invoice_id_stays_none(self) -> None:
        order = {"orderId": "order-1", "correlationId": "corr-1", "amount": "49.90", "invoiceId": None}
        serialized = _serialize_order(order)
        self.assertIsNone(serialized["invoiceId"])


class SerializeSnapshotTest(unittest.TestCase):
    def test_serializes_timestamp_and_ids(self) -> None:
        class _FakeTimestamp:
            def isoformat(self) -> str:
                return "2026-08-23T10:00:00+00:00"

        snapshot = {
            "id": "snap-1",
            "correlationId": "corr-1",
            "previousEventId": "prev-1",
            "timestamp": _FakeTimestamp(),
        }
        serialized = _serialize_snapshot(snapshot)
        self.assertEqual(serialized["timestamp"], "2026-08-23T10:00:00+00:00")
        self.assertEqual(serialized["previousEventId"], "prev-1")

    def test_missing_previous_event_id_stays_none(self) -> None:
        class _FakeTimestamp:
            def isoformat(self) -> str:
                return "2026-08-23T10:00:00+00:00"

        snapshot = {"id": "snap-1", "correlationId": "corr-1", "previousEventId": None, "timestamp": _FakeTimestamp()}
        serialized = _serialize_snapshot(snapshot)
        self.assertIsNone(serialized["previousEventId"])


class TokenHashTest(unittest.TestCase):
    def test_same_token_produces_same_hash(self) -> None:
        self.assertEqual(_token_hash("token-a"), _token_hash("token-a"))

    def test_different_tokens_produce_different_hashes(self) -> None:
        self.assertNotEqual(_token_hash("token-a"), _token_hash("token-b"))

    def test_hash_never_contains_the_raw_token(self) -> None:
        self.assertNotIn("token-a", _token_hash("token-a"))


class RequireAdminTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_cookie_raises_401(self) -> None:
        request = _FakeRequest()
        with self.assertRaises(HTTPException) as ctx:
            await require_admin(request)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_unknown_session_raises_401(self) -> None:
        request = _FakeRequest(cookies={ADMIN_SESSION_COOKIE: "some-token"})
        with patch("src.service.get_admin_session", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                await require_admin(request)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_valid_session_returns_username(self) -> None:
        request = _FakeRequest(cookies={ADMIN_SESSION_COOKIE: "some-token"})
        with patch("src.service.get_admin_session", return_value={"username": "admin"}):
            username = await require_admin(request)
        self.assertEqual(username, "admin")


if __name__ == "__main__":
    unittest.main()
