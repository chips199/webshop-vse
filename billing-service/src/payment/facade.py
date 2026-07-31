"""Payment-Fassade.

Einziger erlaubter Zugriffspunkt fuer Billing-Service-Code auf einen
Zahlungsanbieter. Kapselt gemaess Aufgabenstellung (Abschnitt 3.1 / 9.2):

  - Logging jeder Operation (strukturiert, inkl. correlationId)
  - einheitliche Fehlerbehandlung (Adapter-Exceptions verlassen die
    Fassade nie unveraendert)
  - Retry mit Backoff bei transienten technischen Fehlern

Gibt ausschliesslich Domaenentypen aus payment.models zurueck
(PaymentResult) - niemals anbieterspezifische Objekte/Dicts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from ..config import settings
from .adapters import PaymentAdapter
from .models import PaymentResult

logger = logging.getLogger(__name__)


class PaymentFacadeError(Exception):
    """Einzige Exception, die die Fassade nach aussen durchlaesst.

    Kapselt den urspruenglichen (anbieterspezifischen) Fehler, damit
    Aufrufer der Fassade niemals gegen Stripe-/PayPal-spezifische
    Exception-Typen programmieren muessen.
    """


class PaymentFacade:
    def __init__(
            self,
            adapter: PaymentAdapter,
            max_attempts: int | None = None,
            retry_backoff_seconds: float | None = None,
    ) -> None:
        self._adapter = adapter
        self._max_attempts = max_attempts or settings.payment_retry_max_attempts
        self._retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.payment_retry_backoff_seconds
        )

    @property
    def provider_name(self) -> str:
        return self._adapter.provider_name

    def charge(
            self,
            order_id: str,
            amount: Decimal,
            currency: str,
            payment_method: str | None = None,
            payment_metadata: dict[str, Any] | None = None,
    ) -> PaymentResult:
        correlation_id = (payment_metadata or {}).get("correlationId")
        # Charges werden bewusst NICHT automatisch wiederholt: ein Retry
        # nach einem technischen Fehler (z.B. Timeout) koennte bei einem
        # echten Anbieter zu einer Doppelbuchung fuehren, da wir hier
        # keine Idempotency-Keys garantieren (siehe Bonusaufgabe 4.2).
        return self._execute(
            operation="charge",
            correlation_id=correlation_id,
            context={"orderId": order_id, "amount": str(amount), "currency": currency},
            call=lambda: self._adapter.charge(
                order_id, amount, currency, payment_method, payment_metadata
            ),
            retryable=False,
        )

    def refund(
            self,
            transaction_id: str,
            amount: Decimal,
            correlation_id: str | None = None,
    ) -> PaymentResult:
        return self._execute(
            operation="refund",
            correlation_id=correlation_id,
            context={"transactionId": transaction_id, "amount": str(amount)},
            call=lambda: self._adapter.refund(transaction_id, amount),
            retryable=True,
        )

    def get_status(
            self,
            transaction_id: str,
            correlation_id: str | None = None,
    ) -> PaymentResult:
        # Reiner Lesezugriff -> unkritisch, daher retryable.
        #
        # Damit dieser Retry tatsaechlich greift, reichen StripeAdapter und
        # PayPalAdapter technische Fehler (RuntimeError bei Netzwerk-/HTTP-
        # Fehlern gegen die Sandbox-API) aus get_status() unveraendert bis
        # hierher durch, statt sie selbst in ein FAILED-PaymentResult
        # umzuwandeln (siehe adapters.py). Nur ein fachliches "nicht
        # abgeschlossen" (Session/Order erfolgreich abgefragt, aber nicht
        # bezahlt/completed) liefert direkt FAILED zurueck - das ist kein
        # technischer Fehler und wird bewusst nicht wiederholt.
        return self._execute(
            operation="get_status",
            correlation_id=correlation_id,
            context={"transactionId": transaction_id},
            call=lambda: self._adapter.get_status(transaction_id),
            retryable=True,
        )

    def _execute(
            self,
            *,
            operation: str,
            correlation_id: str | None,
            context: dict[str, Any],
            call: Callable[[], PaymentResult],
            retryable: bool,
    ) -> PaymentResult:
        max_attempts = self._max_attempts if retryable else 1
        attempt = 0
        last_exception: Exception | None = None

        while attempt < max_attempts:
            attempt += 1
            try:
                result = call()
            except Exception as exc:  # Adapter-Exceptions nie unveraendert weiterreichen
                last_exception = exc
                will_retry = attempt < max_attempts
                logger.warning(
                    "Payment operation failed (attempt %s/%s)",
                    attempt,
                    max_attempts,
                    extra={
                        "correlation_id": correlation_id,
                        "context": {
                            **context,
                            "operation": operation,
                            "provider": self.provider_name,
                            "attempt": attempt,
                            "willRetry": will_retry,
                            "error": str(exc),
                        },
                    },
                )
                if will_retry:
                    time.sleep(self._retry_backoff_seconds * attempt)
                    continue
                logger.error(
                    "Payment operation failed permanently",
                    extra={
                        "correlation_id": correlation_id,
                        "context": {
                            **context,
                            "operation": operation,
                            "provider": self.provider_name,
                            "attempts": attempt,
                            "error": str(exc),
                        },
                    },
                )
                raise PaymentFacadeError(
                    f"{operation} via provider '{self.provider_name}' failed "
                    f"after {attempt} attempt(s): {exc}"
                ) from exc
            else:
                logger.info(
                    "Payment operation completed",
                    extra={
                        "correlation_id": correlation_id,
                        "context": {
                            **context,
                            "operation": operation,
                            "provider": self.provider_name,
                            "attempt": attempt,
                            "status": result.status.value,
                            "transactionId": result.transaction_id,
                        },
                    },
                )
                return result

        # Nur zur Absicherung fuer statische Analyse, praktisch unerreichbar.
        raise PaymentFacadeError(str(last_exception))


def get_payment_facade(provider: str | None = None) -> PaymentFacade:
    """Erstellt die Fassade fuer den aktiven Zahlungsanbieter.

    Der Anbieter wird ausschliesslich ueber Konfiguration bestimmt
    (settings.payment_provider, gesetzt per ENV-Variable / .env-Datei) -
    niemals im Code fest verdrahtet. Das optionale `provider`-Argument
    dient nur Tests und dem Fall, dass eine einzelne Order explizit
    einen (weiterhin konfigurierten) Provider referenziert.
    """
    selected = (provider or settings.payment_provider).lower()
    try:
        adapter_class = PaymentAdapter.registry[selected]
    except KeyError as exc:
        supported = ", ".join(sorted(PaymentAdapter.registry))
        raise ValueError(
            f"Unsupported payment provider '{selected}'. Supported: {supported}"
        ) from exc

    logger.info("Payment facade initialized", extra={"context": {"provider": selected}})
    return PaymentFacade(adapter_class())
