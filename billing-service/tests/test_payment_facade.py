from decimal import Decimal
import unittest

from src.config import settings
from src.payment import PaymentStatus, get_payment_facade
from src.payment.adapters import PaymentAdapter
from src.payment.models import PaymentResult


class PaymentFacadeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._stripe_secret_key = settings.stripe_secret_key
        self._paypal_client_id = settings.paypal_client_id
        self._paypal_client_secret = settings.paypal_client_secret
        settings.stripe_secret_key = None
        settings.paypal_client_id = None
        settings.paypal_client_secret = None

    def tearDown(self) -> None:
        settings.stripe_secret_key = self._stripe_secret_key
        settings.paypal_client_id = self._paypal_client_id
        settings.paypal_client_secret = self._paypal_client_secret

    def test_selects_stripe_provider(self) -> None:
        self.assertEqual(get_payment_facade("stripe").provider_name, "stripe")

    def test_selects_paypal_provider(self) -> None:
        self.assertEqual(get_payment_facade("paypal").provider_name, "paypal")

    def test_stripe_charge_succeeds(self) -> None:
        result = get_payment_facade("stripe").charge("order-1", Decimal("49.90"), "EUR")
        self.assertEqual(result.provider, "stripe")
        self.assertEqual(result.status, PaymentStatus.SUCCEEDED)

    def test_paypal_charge_succeeds(self) -> None:
        result = get_payment_facade("paypal").charge("order-2", Decimal("49.90"), "EUR")
        self.assertEqual(result.provider, "paypal")
        self.assertEqual(result.status, PaymentStatus.SUCCEEDED)

    def test_stripe_charge_declined(self) -> None:
        result = get_payment_facade("stripe").charge(
            "order-3",
            Decimal("49.90"),
            "EUR",
            payment_metadata={"scenario": "payment_failed"},
        )
        self.assertEqual(result.status, PaymentStatus.FAILED)
        self.assertEqual(result.reason, "PAYMENT_DECLINED")

    def test_paypal_charge_declined(self) -> None:
        result = get_payment_facade("paypal").charge(
            "order-4",
            Decimal("49.90"),
            "EUR",
            payment_metadata={"scenario": "payment_failed"},
        )
        self.assertEqual(result.status, PaymentStatus.FAILED)
        self.assertEqual(result.reason, "PAYMENT_DECLINED")

    def test_stripe_timeout(self) -> None:
        result = get_payment_facade("stripe").charge(
            "order-5",
            Decimal("49.90"),
            "EUR",
            payment_metadata={"scenario": "payment_timeout"},
        )
        self.assertEqual(result.status, PaymentStatus.FAILED)
        self.assertEqual(result.reason, "PAYMENT_TIMEOUT")

    def test_paypal_timeout(self) -> None:
        result = get_payment_facade("paypal").charge(
            "order-6",
            Decimal("49.90"),
            "EUR",
            payment_metadata={"scenario": "payment_timeout"},
        )
        self.assertEqual(result.status, PaymentStatus.FAILED)
        self.assertEqual(result.reason, "PAYMENT_TIMEOUT")

    def test_refund_and_status(self) -> None:
        refund = get_payment_facade("paypal").refund("paypal-order-1", Decimal("49.90"))
        status = get_payment_facade("stripe").get_status("stripe-order-1")
        self.assertEqual(refund.status, PaymentStatus.REFUNDED)
        self.assertEqual(status.status, PaymentStatus.SUCCEEDED)

    def test_unknown_provider_fails(self) -> None:
        with self.assertRaises(ValueError):
            get_payment_facade("unknown")

    def test_new_provider_can_register_without_facade_change(self) -> None:
        class DemoAdapter(PaymentAdapter):
            provider_name = "demo"

            def charge(self, order_id, amount, currency, payment_method=None, payment_metadata=None):
                return PaymentResult("demo-transaction", self.provider_name, PaymentStatus.SUCCEEDED)

            def refund(self, transaction_id, amount):
                return PaymentResult(transaction_id, self.provider_name, PaymentStatus.REFUNDED)

            def get_status(self, transaction_id):
                return PaymentResult(transaction_id, self.provider_name, PaymentStatus.SUCCEEDED)

        result = get_payment_facade("demo").charge("order-7", Decimal("1.00"), "EUR")
        self.assertEqual(result.provider, "demo")
        self.assertEqual(result.status, PaymentStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
