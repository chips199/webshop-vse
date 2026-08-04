from decimal import Decimal
import unittest
from unittest.mock import patch

from src.config import settings
from src.payment import PaymentFacade, PaymentFacadeError, PaymentStatus, get_payment_facade
from src.payment.adapters import PaymentAdapter, PayPalAdapter, StripeAdapter
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

    def test_paypal_charge_without_credentials_returns_pending_and_schedules_webhook(self) -> None:
        # Ohne Sandbox-Credentials ist PayPal ein Stub, der Bonus 4.4 umsetzt:
        # charge() liefert sofort PENDING, das Ergebnis kommt per Timer-Webhook nach.
        with patch.object(PayPalAdapter, "_schedule_webhook") as schedule_webhook:
            result = get_payment_facade("paypal").charge("order-2", Decimal("49.90"), "EUR")
        self.assertEqual(result.provider, "paypal")
        self.assertEqual(result.status, PaymentStatus.PENDING)
        schedule_webhook.assert_called_once()

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

    def test_paypal_stub_charge_schedules_success_webhook(self) -> None:
        with patch.object(PayPalAdapter, "_schedule_webhook") as schedule_webhook:
            result = get_payment_facade("paypal").charge(
                "order-8",
                Decimal("49.90"),
                "EUR",
                payment_metadata={
                    "correlationId": "corr-8",
                    "previousEventId": "event-7",
                },
            )

        self.assertEqual(result.provider, "paypal")
        self.assertEqual(result.status, PaymentStatus.PENDING)
        payload = schedule_webhook.call_args.args[0]
        self.assertEqual(payload["orderId"], "order-8")
        self.assertEqual(payload["transactionId"], "paypal-order-8")
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertEqual(payload["correlationId"], "corr-8")
        self.assertEqual(payload["previousEventId"], "event-7")

    def test_paypal_stub_can_schedule_failure_webhook(self) -> None:
        with patch.object(PayPalAdapter, "_schedule_webhook") as schedule_webhook:
            result = get_payment_facade("paypal").charge(
                "order-9",
                Decimal("29.90"),
                "EUR",
                payment_metadata={
                    "correlationId": "corr-9",
                    "previousEventId": "event-8",
                    "webhookStatus": "FAILED",
                    "webhookReasonCode": "ASYNC_DECLINED",
                },
            )

        self.assertEqual(result.status, PaymentStatus.PENDING)
        payload = schedule_webhook.call_args.args[0]
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["reasonCode"], "ASYNC_DECLINED")

    # -- get_status()-Retry bei technischen Fehlern (Bereinigung Punkt 2) --
    #
    # Mit Sandbox-Credentials reichen StripeAdapter/PayPalAdapter technische
    # Fehler (RuntimeError bei Netzwerk-/HTTP-Problemen) jetzt bis zur
    # Fassade durch, statt sie selbst in FAILED umzuwandeln. Die folgenden
    # Tests verifizieren, dass der Retry dadurch tatsaechlich mehrfach
    # aufruft und am Ende PaymentFacadeError wirft, waehrend eine fachlich
    # nicht abgeschlossene (aber technisch erfolgreich abgefragte) Zahlung
    # weiterhin direkt und ohne Retry als FAILED zurueckkommt.

    def test_stripe_get_status_retries_on_technical_error_then_raises(self) -> None:
        settings.stripe_secret_key = "sk_test_dummy"
        with patch.object(
            StripeAdapter, "retrieve_session", side_effect=RuntimeError("Stripe sandbox request failed")
        ) as retrieve_session:
            facade = PaymentFacade(StripeAdapter(), max_attempts=2, retry_backoff_seconds=0)
            with self.assertRaises(PaymentFacadeError):
                facade.get_status("cs_test_123")
        self.assertEqual(retrieve_session.call_count, 2)

    def test_stripe_get_status_business_failure_is_not_retried(self) -> None:
        settings.stripe_secret_key = "sk_test_dummy"
        with patch.object(
            StripeAdapter,
            "retrieve_session",
            return_value={"sessionId": "cs_test_123", "paymentStatus": "unpaid"},
        ) as retrieve_session:
            facade = PaymentFacade(StripeAdapter(), max_attempts=3, retry_backoff_seconds=0)
            result = facade.get_status("cs_test_123")
        self.assertEqual(result.status, PaymentStatus.FAILED)
        retrieve_session.assert_called_once()

    def test_paypal_get_status_retries_on_technical_error_then_raises(self) -> None:
        settings.paypal_client_id = "client-id"
        settings.paypal_client_secret = "client-secret"
        with patch.object(
            PayPalAdapter, "capture_order", side_effect=RuntimeError("PayPal sandbox request failed")
        ) as capture_order:
            facade = PaymentFacade(PayPalAdapter(), max_attempts=2, retry_backoff_seconds=0)
            with self.assertRaises(PaymentFacadeError):
                facade.get_status("8AC12345XA111111B")
        self.assertEqual(capture_order.call_count, 2)

    def test_paypal_get_status_business_failure_is_not_retried(self) -> None:
        settings.paypal_client_id = "client-id"
        settings.paypal_client_secret = "client-secret"
        with patch.object(
            PayPalAdapter,
            "capture_order",
            return_value={"orderId": "8AC12345XA222222B", "status": "VOIDED"},
        ) as capture_order:
            facade = PaymentFacade(PayPalAdapter(), max_attempts=3, retry_backoff_seconds=0)
            result = facade.get_status("8AC12345XA222222B")
        self.assertEqual(result.status, PaymentStatus.FAILED)
        capture_order.assert_called_once()

    # -- transaction_id-Umschwenk auf die PaymentIntent-Id (Refund-Bugfix) --
    #
    # get_status() gab bei einer bezahlten Stripe-Session bisher weiterhin
    # die Checkout-Session-Id ("cs_...") als transaction_id zurueck. Stripes
    # Refund-Endpunkt akzeptiert aber nur eine PaymentIntent-Id ("pi_..."),
    # wodurch refund() mit echten Credentials nie tatsaechlich einen
    # Refund ausgeloest hat (transaction_id.startswith("pi_") war nie wahr).
    # Der folgende Test verifiziert, dass get_status() jetzt korrekt auf
    # "paymentIntentId" umschwenkt, sobald retrieve_session() eine liefert -
    # analog zum bereits bestehenden captureId-Tausch bei PayPal.

    def test_stripe_get_status_switches_to_payment_intent_id_on_success(self) -> None:
        settings.stripe_secret_key = "sk_test_dummy"
        with patch.object(
            StripeAdapter,
            "retrieve_session",
            return_value={
                "sessionId": "cs_test_123",
                "paymentIntentId": "pi_test_456",
                "paymentStatus": "paid",
            },
        ):
            result = get_payment_facade("stripe").get_status("cs_test_123")
        self.assertEqual(result.status, PaymentStatus.SUCCEEDED)
        self.assertEqual(result.transaction_id, "pi_test_456")

    def test_stripe_get_status_falls_back_to_session_id_without_payment_intent(self) -> None:
        # Fehlt "payment_intent" in der Stripe-Antwort (sollte bei einer
        # bezahlten Session im "payment"-Modus nicht vorkommen, aber
        # verteidigt gegen unerwartete API-Antworten), bleibt get_status()
        # bei der urspruenglichen Session-Id statt None zurueckzugeben.
        settings.stripe_secret_key = "sk_test_dummy"
        with patch.object(
            StripeAdapter,
            "retrieve_session",
            return_value={"sessionId": "cs_test_123", "paymentStatus": "paid"},
        ):
            result = get_payment_facade("stripe").get_status("cs_test_123")
        self.assertEqual(result.transaction_id, "cs_test_123")

    # -- Kundendaten aus der Sandbox uebernehmen --
    #
    # Mit Sandbox-Credentials liefern Stripe/PayPal beim erfolgreichen
    # get_status() echte Kaeufer-/Adressdaten mit, wenn der Kaeufer sie auf
    # der Anbieter-Seite eingegeben hat. Die folgenden Tests verifizieren,
    # dass PaymentResult.customer/.shipping_address dann befuellt sind, und
    # dass reine Platzhalter-Antworten (keine echten Daten vom Anbieter)
    # NICHT durchgereicht werden.

    def test_stripe_get_status_returns_real_customer_and_shipping(self) -> None:
        settings.stripe_secret_key = "sk_test_dummy"
        with patch.object(
            StripeAdapter,
            "retrieve_session",
            return_value={
                "sessionId": "cs_test_123",
                "paymentStatus": "paid",
                "customer": {
                    "firstName": "Grace",
                    "lastName": "Hopper",
                    "email": "grace.hopper@example.test",
                    "phone": "",
                },
                "shippingAddress": {
                    "street": "Turingstrasse",
                    "houseNumber": "1",
                    "postalCode": "10115",
                    "city": "Berlin",
                    "country": "DE",
                    "recipientName": "Grace Hopper",
                },
            },
        ):
            result = get_payment_facade("stripe").get_status("cs_test_123")
        self.assertEqual(result.customer["email"], "grace.hopper@example.test")
        self.assertEqual(result.shipping_address["street"], "Turingstrasse")

    def test_stripe_get_status_omits_placeholder_customer_and_shipping(self) -> None:
        settings.stripe_secret_key = "sk_test_dummy"
        with patch.object(
            StripeAdapter,
            "retrieve_session",
            return_value={
                "sessionId": "cs_test_123",
                "paymentStatus": "paid",
                "customer": {"firstName": "", "lastName": "", "email": "", "phone": ""},
                "shippingAddress": {
                    "street": "Stripe-Adresse",
                    "houseNumber": "-",
                    "postalCode": "-",
                    "city": "-",
                    "country": "-",
                    "recipientName": "",
                },
            },
        ):
            result = get_payment_facade("stripe").get_status("cs_test_123")
        self.assertIsNone(result.customer)
        self.assertIsNone(result.shipping_address)

    def test_paypal_get_status_returns_real_customer_and_shipping(self) -> None:
        settings.paypal_client_id = "client-id"
        settings.paypal_client_secret = "client-secret"
        with patch.object(
            PayPalAdapter,
            "capture_order",
            return_value={
                "orderId": "8AC12345XA333333B",
                "captureId": "CAP-1",
                "status": "COMPLETED",
                "payer": {
                    "firstName": "Grace",
                    "lastName": "Hopper",
                    "email": "grace.hopper@example.test",
                    "payerId": "PAYERID1",
                },
                "shippingAddress": {
                    "street": "Turingstrasse",
                    "houseNumber": "1",
                    "postalCode": "10115",
                    "city": "Berlin",
                    "country": "DE",
                    "recipientName": "Grace Hopper",
                },
            },
        ):
            result = get_payment_facade("paypal").get_status("8AC12345XA333333B")
        self.assertEqual(result.customer["email"], "grace.hopper@example.test")
        self.assertEqual(result.shipping_address["city"], "Berlin")

    def test_paypal_get_status_omits_placeholder_customer_and_shipping(self) -> None:
        settings.paypal_client_id = "client-id"
        settings.paypal_client_secret = "client-secret"
        with patch.object(
            PayPalAdapter,
            "capture_order",
            return_value={
                "orderId": "8AC12345XA444444B",
                "captureId": "CAP-2",
                "status": "COMPLETED",
                "payer": {"firstName": "", "lastName": "", "email": "", "payerId": ""},
                "shippingAddress": None,
            },
        ):
            result = get_payment_facade("paypal").get_status("8AC12345XA444444B")
        self.assertIsNone(result.customer)
        self.assertIsNone(result.shipping_address)


if __name__ == "__main__":
    unittest.main()
