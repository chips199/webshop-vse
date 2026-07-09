from decimal import Decimal
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import settings
from .models import PaymentResult, PaymentStatus


class PaymentAdapter:
    provider_name: str

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
    ) -> PaymentResult:
        raise NotImplementedError

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        raise NotImplementedError

    def get_status(self, transaction_id: str) -> PaymentResult:
        raise NotImplementedError


class StripeAdapter(PaymentAdapter):
    provider_name = "stripe"

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
    ) -> PaymentResult:
        if settings.stripe_secret_key:
            return self._charge_with_stripe(order_id, amount, currency, payment_method)
        return PaymentResult(
            transaction_id=f"stripe-{order_id}",
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        if settings.stripe_secret_key and transaction_id.startswith("pi_"):
            self._request(
                "https://api.stripe.com/v1/refunds",
                {
                    "payment_intent": transaction_id,
                    "amount": _minor_units(amount),
                },
            )
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.REFUNDED,
        )

    def get_status(self, transaction_id: str) -> PaymentResult:
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def _charge_with_stripe(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
    ) -> PaymentResult:
        response = self._request(
            "https://api.stripe.com/v1/payment_intents",
            {
                "amount": _minor_units(amount),
                "currency": currency.lower(),
                "payment_method": payment_method or settings.stripe_payment_method,
                "confirm": "true",
                "automatic_payment_methods[enabled]": "true",
                "automatic_payment_methods[allow_redirects]": "never",
                "description": f"Historical computer parts order {order_id}",
                "metadata[orderId]": order_id,
            },
        )
        status = response.get("status")
        if status in {"succeeded", "processing", "requires_capture"}:
            return PaymentResult(
                transaction_id=response["id"],
                provider=self.provider_name,
                status=PaymentStatus.SUCCEEDED,
            )
        return PaymentResult(
            transaction_id=response.get("id", f"stripe-{order_id}"),
            provider=self.provider_name,
            status=PaymentStatus.FAILED,
            reason=f"Stripe PaymentIntent status {status}",
        )

    def _request(self, url: str, data: dict) -> dict:
        request = Request(
            url,
            data=urlencode(data).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.stripe_secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Stripe sandbox request failed: {exc}") from exc


class PayPalAdapter(PaymentAdapter):
    provider_name = "paypal"

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
    ) -> PaymentResult:
        if settings.paypal_client_id and settings.paypal_client_secret:
            return self._create_paypal_order(order_id, amount, currency)
        return PaymentResult(
            transaction_id=f"paypal-{order_id}",
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.REFUNDED,
        )

    def get_status(self, transaction_id: str) -> PaymentResult:
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def _create_paypal_order(self, order_id: str, amount: Decimal, currency: str) -> PaymentResult:
        access_token = self._access_token()
        request = Request(
            f"{settings.paypal_base_url}/v2/checkout/orders",
            data=json.dumps(
                {
                    "intent": "CAPTURE",
                    "purchase_units": [
                        {
                            "reference_id": order_id,
                            "amount": {
                                "currency_code": currency.upper(),
                                "value": f"{amount:.2f}",
                            },
                        }
                    ],
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"PayPal sandbox order request failed: {exc}") from exc
        if body.get("id"):
            return PaymentResult(
                transaction_id=body["id"],
                provider=self.provider_name,
                status=PaymentStatus.SUCCEEDED,
                reason=f"PayPal sandbox order status {body.get('status')}",
            )
        return PaymentResult(
            transaction_id=f"paypal-{order_id}",
            provider=self.provider_name,
            status=PaymentStatus.FAILED,
            reason="PayPal sandbox did not return an order id",
        )

    def _access_token(self) -> str:
        request = Request(
            f"{settings.paypal_base_url}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        import base64

        credentials = f"{settings.paypal_client_id}:{settings.paypal_client_secret}".encode("utf-8")
        request.add_header("Authorization", f"Basic {base64.b64encode(credentials).decode('ascii')}")
        try:
            with urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"PayPal sandbox auth failed: {exc}") from exc
        return body["access_token"]


def _minor_units(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))
