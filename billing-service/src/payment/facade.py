from decimal import Decimal

from .adapters import PayPalAdapter, PaymentAdapter, StripeAdapter
from .models import PaymentResult


class PaymentFacade:
    def __init__(self, adapter: PaymentAdapter) -> None:
        self._adapter = adapter

    @property
    def provider_name(self) -> str:
        return self._adapter.provider_name

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
        payment_metadata: dict | None = None,
    ) -> PaymentResult:
        return self._adapter.charge(order_id, amount, currency, payment_method, payment_metadata)

    def create_paypal_order(
        self,
        reference_id: str,
        amount: Decimal,
        currency: str,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict:
        if not hasattr(self._adapter, "create_order"):
            raise ValueError("Current payment adapter does not support PayPal orders")
        return self._adapter.create_order(reference_id, amount, currency, return_url, cancel_url)

    def capture_paypal_order(self, paypal_order_id: str) -> dict:
        if not hasattr(self._adapter, "capture_order"):
            raise ValueError("Current payment adapter does not support PayPal captures")
        return self._adapter.capture_order(paypal_order_id)

    def create_stripe_session(
        self,
        reference_id: str,
        amount: Decimal,
        currency: str,
        success_url: str | None = None,
        cancel_url: str | None = None,
        customer_email: str | None = None,
        items: list[dict] | None = None,
    ) -> dict:
        if not hasattr(self._adapter, "create_session"):
            raise ValueError("Current payment adapter does not support Stripe sessions")
        return self._adapter.create_session(reference_id, amount, currency, success_url, cancel_url, customer_email, items)

    def retrieve_stripe_session(self, session_id: str) -> dict:
        if not hasattr(self._adapter, "retrieve_session"):
            raise ValueError("Current payment adapter does not support Stripe sessions")
        return self._adapter.retrieve_session(session_id)

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        return self._adapter.refund(transaction_id, amount)

    def get_status(self, transaction_id: str) -> PaymentResult:
        return self._adapter.get_status(transaction_id)


def get_payment_facade(provider: str) -> PaymentFacade:
    adapters: dict[str, PaymentAdapter] = {
        "stripe": StripeAdapter(),
        "paypal": PayPalAdapter(),
    }
    try:
        return PaymentFacade(adapters[provider.lower()])
    except KeyError as exc:
        supported = ", ".join(sorted(adapters))
        raise ValueError(f"Unsupported payment provider '{provider}'. Supported: {supported}") from exc
