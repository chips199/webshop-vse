from dataclasses import dataclass
from enum import Enum


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


@dataclass(frozen=True)
class PaymentResult:
    transaction_id: str
    provider: str
    status: PaymentStatus
    reason: str | None = None
    redirect_url: str | None = None
    # Nur befuellt, wenn der Anbieter mit Sandbox-Credentials laeuft und der
    # Kaeufer die Daten tatsaechlich auf der echten Stripe-/PayPal-Seite
    # eingegeben hat (siehe get_status() in adapters.py). Ueberschreibt dann
    # die beim Checkout im eigenen Formular erfassten Werte.
    customer: dict | None = None
    shipping_address: dict | None = None
