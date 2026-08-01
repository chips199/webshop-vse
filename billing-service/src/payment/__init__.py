# Oeffentliche Schnittstelle des payment-Package: Code ausserhalb dieses
# Packages soll ausschliesslich ueber diese Namen mit Zahlungen arbeiten
# (allen voran get_payment_facade()) und nie direkt die Adapter aus
# adapters.py importieren - genau das erzwingt den Fassaden-Charakter.
from .facade import PaymentFacade, PaymentFacadeError, get_payment_facade
from .models import PaymentResult, PaymentStatus

__all__ = [
    "PaymentFacade",
    "PaymentFacadeError",
    "PaymentResult",
    "PaymentStatus",
    "get_payment_facade",
]
