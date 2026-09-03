from dataclasses import dataclass
from enum import Enum


class PaymentStatus(str, Enum):
    """Anbieterunabhaengiger Zahlungsstatus."""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


@dataclass(frozen=True)
class PaymentResult:
    """Unveraenderliches Ergebnis einer Zahlungsoperation."""

    transaction_id: str
    provider: str
    status: PaymentStatus
    # Lesbare Statusbeschreibung.
    reason: str | None = None
    # Weiterleitungsziel bei PENDING.
    redirect_url: str | None = None
    # Optionale Kunden- und Adressdaten des Anbieters.
    customer: dict | None = None
    shipping_address: dict | None = None
