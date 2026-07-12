from decimal import Decimal
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import settings
from .models import PaymentResult, PaymentStatus


class PaymentAdapter:
    provider_name: str
    registry: dict[str, type["PaymentAdapter"]] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        provider_name = getattr(cls, "provider_name", None)
        if provider_name:
            PaymentAdapter.registry[provider_name] = cls

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
        payment_metadata: dict | None = None,
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
        payment_metadata: dict | None = None,
    ) -> PaymentResult:
        payment_metadata = payment_metadata or {}
        simulated = _simulated_result(self.provider_name, order_id, payment_metadata)
        if simulated:
            return simulated
        if payment_metadata.get("stripeSessionId"):
            return PaymentResult(
                transaction_id=payment_metadata["stripeSessionId"],
                provider=self.provider_name,
                status=PaymentStatus.SUCCEEDED,
                reason=f"Stripe checkout session {payment_metadata.get('stripeSessionStatus')} paid",
            )
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

    def create_session(
        self,
        reference_id: str,
        amount: Decimal,
        currency: str,
        success_url: str | None = None,
        cancel_url: str | None = None,
        customer_email: str | None = None,
        items: list[dict] | None = None,
    ) -> dict:
        if not settings.stripe_secret_key:
            return {
                "sessionId": f"stripe-stub-{reference_id}",
                "status": "complete",
                "paymentStatus": "paid",
                "checkoutUrl": success_url or "http://localhost:3000/checkout?stripe=approved",
                "stub": True,
            }

        data = {
            "mode": "payment",
            "success_url": success_url or "http://localhost:3000/checkout?stripe=approved&session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url or "http://localhost:3000/checkout?stripe=cancelled",
            "client_reference_id": reference_id,
            "payment_method_types[0]": "card",
            "billing_address_collection": "required",
            "shipping_address_collection[allowed_countries][0]": "DE",
            "shipping_address_collection[allowed_countries][1]": "AT",
            "shipping_address_collection[allowed_countries][2]": "CH",
            "metadata[referenceId]": reference_id,
        }
        if customer_email:
            data["customer_email"] = customer_email

        line_items = items or [
            {
                "name": "Retro Parts Bestellung",
                "amount": amount,
                "quantity": 1,
            }
        ]
        for index, item in enumerate(line_items):
            item_amount = item.get("amount") or item.get("price") or amount
            data[f"line_items[{index}][quantity]"] = int(item.get("quantity", 1))
            data[f"line_items[{index}][price_data][currency]"] = currency.lower()
            data[f"line_items[{index}][price_data][unit_amount]"] = _minor_units(Decimal(str(item_amount)))
            data[f"line_items[{index}][price_data][product_data][name]"] = item.get("name", "Retro Parts Artikel")

        body = self._request("https://api.stripe.com/v1/checkout/sessions", data)
        return {
            "sessionId": body["id"],
            "status": body.get("status", "open"),
            "paymentStatus": body.get("payment_status", "unpaid"),
            "checkoutUrl": body.get("url"),
            "stub": False,
        }

    def retrieve_session(self, session_id: str) -> dict:
        if session_id.startswith("stripe-stub-"):
            return {
                "sessionId": session_id,
                "status": "complete",
                "paymentStatus": "paid",
                "customer": {
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "email": "ada.lovelace@example.test",
                },
                "shippingAddress": {
                    "street": "Retroallee",
                    "houseNumber": "8",
                    "postalCode": "10115",
                    "city": "Berlin",
                    "country": "DE",
                },
                "stub": True,
            }

        body = self._request(f"https://api.stripe.com/v1/checkout/sessions/{session_id}", {}, method="GET")
        return {
            "sessionId": body["id"],
            "status": body.get("status"),
            "paymentStatus": body.get("payment_status"),
            "customer": _normalize_stripe_customer(body.get("customer_details", {})),
            "shippingAddress": _normalize_stripe_shipping(_stripe_shipping_details(body)),
            "stub": False,
        }

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

    def _request(self, url: str, data: dict, method: str = "POST") -> dict:
        encoded_data = urlencode(data).encode("utf-8") if method != "GET" else None
        request = Request(
            url,
            data=encoded_data,
            headers={
                "Authorization": f"Bearer {settings.stripe_secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method=method,
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
        payment_metadata: dict | None = None,
    ) -> PaymentResult:
        payment_metadata = payment_metadata or {}
        simulated = _simulated_result(self.provider_name, order_id, payment_metadata)
        if simulated:
            return simulated
        if payment_metadata.get("paypalCaptureId"):
            return PaymentResult(
                transaction_id=payment_metadata["paypalCaptureId"],
                provider=self.provider_name,
                status=PaymentStatus.SUCCEEDED,
                reason=f"PayPal sandbox order {payment_metadata.get('paypalOrderId')} captured",
            )
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

    def create_order(
        self,
        reference_id: str,
        amount: Decimal,
        currency: str,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict:
        if not settings.paypal_client_id or not settings.paypal_client_secret:
            return {
                "orderId": f"paypal-stub-{reference_id}",
                "status": "CREATED",
                "approveUrl": None,
                "stub": True,
            }
        body = self._paypal_json_request(
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": reference_id,
                        "amount": {
                            "currency_code": currency.upper(),
                            "value": f"{amount:.2f}",
                        },
                    }
                ],
                "application_context": {
                    "brand_name": "Retro Parts Terminal",
                    "shipping_preference": "GET_FROM_FILE",
                    "user_action": "PAY_NOW",
                    "return_url": return_url or "http://localhost:3000/checkout?paypal=approved",
                    "cancel_url": cancel_url or "http://localhost:3000/checkout?paypal=cancelled",
                },
            },
        )
        approve_url = next(
            (link["href"] for link in body.get("links", []) if link.get("rel") == "approve"),
            None,
        )
        return {
            "orderId": body["id"],
            "status": body.get("status", "CREATED"),
            "approveUrl": approve_url,
            "stub": False,
        }

    def capture_order(self, paypal_order_id: str) -> dict:
        if paypal_order_id.startswith("paypal-stub-"):
            return {
                "orderId": paypal_order_id,
                "captureId": f"capture-{paypal_order_id}",
                "status": "COMPLETED",
                "payer": {
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "email": "buyer@example.test",
                },
                "shippingAddress": {
                    "street": "Retroallee",
                    "houseNumber": "8",
                    "postalCode": "10115",
                    "city": "Berlin",
                    "country": "DE",
                },
                "stub": True,
            }
        body = self._paypal_json_request(f"/v2/checkout/orders/{paypal_order_id}/capture", {})
        capture_id = None
        shipping_address = None
        for unit in body.get("purchase_units", []):
            captures = unit.get("payments", {}).get("captures", [])
            if captures:
                capture_id = captures[0].get("id")
            if unit.get("shipping"):
                shipping_address = _normalize_paypal_shipping(unit["shipping"])
        return {
            "orderId": body["id"],
            "captureId": capture_id or body["id"],
            "status": body.get("status", "COMPLETED"),
            "payer": _normalize_paypal_payer(body.get("payer", {})),
            "shippingAddress": shipping_address,
            "stub": False,
        }

    def _create_paypal_order(self, order_id: str, amount: Decimal, currency: str) -> PaymentResult:
        body = self.create_order(order_id, amount, currency)
        if body.get("orderId"):
            return PaymentResult(
                transaction_id=body["orderId"],
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

    def _paypal_json_request(self, path: str, payload: dict) -> dict:
        access_token = self._access_token()
        request = Request(
            f"{settings.paypal_base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"PayPal sandbox request failed: {exc}") from exc

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


def _simulated_result(provider: str, order_id: str, payment_metadata: dict) -> PaymentResult | None:
    scenario = payment_metadata.get("scenario")
    if scenario == "payment_failed":
        return PaymentResult(
            transaction_id=f"{provider}-{order_id}",
            provider=provider,
            status=PaymentStatus.FAILED,
            reason="PAYMENT_DECLINED",
        )
    if scenario == "payment_timeout":
        return PaymentResult(
            transaction_id=f"{provider}-{order_id}",
            provider=provider,
            status=PaymentStatus.FAILED,
            reason="PAYMENT_TIMEOUT",
        )
    return None


def _normalize_paypal_payer(payer: dict) -> dict:
    name = payer.get("name", {})
    return {
        "firstName": name.get("given_name") or "",
        "lastName": name.get("surname") or "",
        "email": payer.get("email_address") or "",
        "payerId": payer.get("payer_id") or "",
    }


def _normalize_paypal_shipping(shipping: dict) -> dict:
    address = shipping.get("address", {})
    street, house_number = _split_street_and_house_number(address.get("address_line_1") or "")
    return {
        "street": street or address.get("address_line_1") or "PayPal-Adresse",
        "houseNumber": house_number or "-",
        "postalCode": address.get("postal_code") or "-",
        "city": address.get("admin_area_2") or address.get("admin_area_1") or "-",
        "country": address.get("country_code") or "-",
        "recipientName": shipping.get("name", {}).get("full_name") or "",
    }


def _split_street_and_house_number(address_line: str) -> tuple[str, str]:
    parts = address_line.strip().rsplit(" ", 1)
    if len(parts) == 2 and any(char.isdigit() for char in parts[1]):
        return parts[0], parts[1]
    return address_line.strip(), ""


def _normalize_stripe_customer(customer_details: dict) -> dict:
    name = customer_details.get("name") or ""
    first_name, last_name = _split_full_name(name)
    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": customer_details.get("email") or "",
        "phone": customer_details.get("phone") or "",
    }


def _normalize_stripe_shipping(shipping_details: dict) -> dict:
    address = shipping_details.get("address", {})
    street, house_number = _split_street_and_house_number(address.get("line1") or "")
    return {
        "street": street or address.get("line1") or "Stripe-Adresse",
        "houseNumber": house_number or "-",
        "postalCode": address.get("postal_code") or "-",
        "city": address.get("city") or "-",
        "country": address.get("country") or "-",
        "recipientName": shipping_details.get("name") or "",
    }


def _stripe_shipping_details(session: dict) -> dict:
    if session.get("shipping_details"):
        return session["shipping_details"]
    collected = session.get("collected_information") or {}
    if collected.get("shipping_details"):
        return collected["shipping_details"]
    customer_details = session.get("customer_details") or {}
    if customer_details.get("address"):
        return {
            "address": customer_details["address"],
            "name": customer_details.get("name") or "",
        }
    return {}


def _split_full_name(name: str) -> tuple[str, str]:
    parts = name.strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return name.strip() or "Stripe", "Kunde"
