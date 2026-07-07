from decimal import Decimal

from .adapters import PayPalAdapter, PaymentAdapter, StripeAdapter
from .models import PaymentResult


class PaymentFacade:
    def __init__(self, adapter: PaymentAdapter) -> None:
        self._adapter = adapter

    @property
    def provider_name(self) -> str:
        return self._adapter.provider_name

    def charge(self, order_id: str, amount: Decimal, currency: str) -> PaymentResult:
        return self._adapter.charge(order_id, amount, currency)

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
