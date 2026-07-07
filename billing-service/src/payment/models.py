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
