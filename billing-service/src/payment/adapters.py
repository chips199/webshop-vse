from decimal import Decimal

from .models import PaymentResult, PaymentStatus


class PaymentAdapter:
    provider_name: str

    def charge(self, order_id: str, amount: Decimal, currency: str) -> PaymentResult:
        raise NotImplementedError

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        raise NotImplementedError

    def get_status(self, transaction_id: str) -> PaymentResult:
        raise NotImplementedError


class StripeAdapter(PaymentAdapter):
    provider_name = "stripe"

    def charge(self, order_id: str, amount: Decimal, currency: str) -> PaymentResult:
        return PaymentResult(
            transaction_id=f"stripe-{order_id}",
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


class PayPalAdapter(PaymentAdapter):
    provider_name = "paypal"

    def charge(self, order_id: str, amount: Decimal, currency: str) -> PaymentResult:
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
