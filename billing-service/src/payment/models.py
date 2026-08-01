from dataclasses import dataclass
from enum import Enum


class PaymentStatus(str, Enum):
    """Anbieterunabhaengiger Zahlungsstatus, den die Fassade nach aussen gibt.

    Erbt zusaetzlich von str, damit z.B. `status.value` bzw. der Enum selbst
    direkt in JSON-Payloads (RabbitMQ-Events, API-Responses) verwendet werden
    kann, ohne manuell zu konvertieren.
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


@dataclass(frozen=True)
class PaymentResult:
    """Einheitlicher Rueckgabetyp fuer jede Adapter-/Fassaden-Operation.

    "frozen=True" macht Instanzen unveraenderlich (wie ein Value Object) -
    ein Aufrufer kann ein PaymentResult also nicht versehentlich nachtraeglich
    mutieren. Alle Felder ausser den ersten drei sind optional, weil nicht
    jede Operation (charge/refund/get_status) jedes Feld befuellt.
    """

    transaction_id: str
    provider: str
    status: PaymentStatus
    # Menschlich lesbarer Grund, z.B. bei FAILED ("PAYMENT_DECLINED") oder
    # SUCCEEDED (Bestaetigungstext) - nicht fuer Programmlogik gedacht.
    reason: str | None = None
    # Nur bei PENDING gesetzt: URL, zu der der Kaeufer fuer die echte
    # Stripe-/PayPal-Sandbox-Zahlung weitergeleitet werden muss.
    redirect_url: str | None = None
    # Nur befuellt, wenn der Anbieter mit Sandbox-Credentials laeuft und der
    # Kaeufer die Daten tatsaechlich auf der echten Stripe-/PayPal-Seite
    # eingegeben hat (siehe get_status() in adapters.py). Ueberschreibt dann
    # die beim Checkout im eigenen Formular erfassten Werte.
    customer: dict | None = None
    shipping_address: dict | None = None
