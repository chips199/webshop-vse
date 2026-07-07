from decimal import Decimal
import unittest

from src.payment import PaymentStatus, get_payment_facade


class PaymentFacadeTest(unittest.TestCase):
    def test_selects_stripe_provider(self) -> None:
        facade = get_payment_facade("stripe")
        self.assertEqual(facade.provider_name, "stripe")

    def test_selects_paypal_provider(self) -> None:
        facade = get_payment_facade("paypal")
        self.assertEqual(facade.provider_name, "paypal")

    def test_stripe_charge_succeeds(self) -> None:
        result = get_payment_facade("stripe").charge("order-1", Decimal("49.90"), "EUR")
        self.assertEqual(result.provider, "stripe")
        self.assertEqual(result.status, PaymentStatus.SUCCEEDED)

    def test_paypal_refund_succeeds(self) -> None:
        result = get_payment_facade("paypal").refund("paypal-order-1", Decimal("49.90"))
        self.assertEqual(result.provider, "paypal")
        self.assertEqual(result.status, PaymentStatus.REFUNDED)

    def test_unknown_provider_fails(self) -> None:
        with self.assertRaises(ValueError):
            get_payment_facade("unknown")


if __name__ == "__main__":
    unittest.main()
