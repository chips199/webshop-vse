"""Zahlungsverarbeitung des Billing-Service."""

from decimal import Decimal
import logging

from .config import settings
from .messaging import build_message, publish_message
from .payment import PaymentFacadeError, get_payment_facade

logger = logging.getLogger(__name__)


def publish_payment_result(
        *,
        status: str,
        correlation_id: str,
        previous_event_id: str | None,
        order_id: str,
        transaction_id: str | None,
        provider: str,
        amount: str,
        currency: str,
        scenario: str = "happy_path",
        reason_code: str | None = None,
        message: str | None = None,
        customer: dict | None = None,
        shipping_address: dict | None = None,
) -> None:
    """Publiziert das Saga-Ereignis eines Zahlungsergebnisses."""
    if status == "SUCCEEDED":
        event_type = "billing.payment.succeeded"
        event_payload = {
            "orderId": order_id,
            "transactionId": transaction_id,
            "provider": provider,
            "amount": amount,
            "currency": currency,
            "scenario": scenario,
            "paymentStatus": status,
        }
        # Optionale Kundendaten stammen vom Zahlungsanbieter.
        if customer:
            event_payload["customer"] = customer
        if shipping_address:
            event_payload["shippingAddress"] = shipping_address
    else:
        event_type = "billing.payment.failed"
        payment_result = "TIMEOUT" if reason_code == "PAYMENT_TIMEOUT" else "DECLINED"
        event_payload = {
            "orderId": order_id,
            "provider": provider,
            "amount": amount,
            "currency": currency,
            "reasonCode": reason_code or "PAYMENT_DECLINED",
            "message": message or "Payment provider did not approve the payment.",
        }
        if transaction_id:
            event_payload["transactionId"] = transaction_id
    if status == "SUCCEEDED":
        payment_result = "SUCCEEDED"
    event = build_message(
        event_type,
        correlation_id,
        event_payload,
        previous_event_id=previous_event_id,
    )
    publish_message(event_type, event)
    logger.info(
        "Payment attempt finished",
        extra={
            "correlation_id": correlation_id,
            "context": {
                "eventType": event_type,
                "orderId": order_id,
                "provider": provider,
                "paymentStatus": status,
                "paymentResult": payment_result,
                "reasonCode": reason_code,
            },
        },
    )


def handle_billing_message(message: dict) -> None:
    """Verarbeitet Zahlungs-, Bestaetigungs- und Erstattungs-Commands."""
    if message["type"] == "billing.payment.confirm.requested":
        payload = message.get("payload", {})
        provider = payload.get("provider") or settings.payment_provider
        facade = get_payment_facade(provider)
        try:
            result = facade.get_status(payload["transactionId"], correlation_id=message["correlationId"])
        except PaymentFacadeError as exc:
            publish_payment_result(
                status="FAILED",
                correlation_id=message["correlationId"],
                previous_event_id=message["messageId"],
                order_id=payload["orderId"],
                transaction_id=payload.get("transactionId"),
                provider=provider,
                amount=payload["amount"],
                currency=payload["currency"],
                reason_code="PAYMENT_PROVIDER_ERROR",
                message=str(exc),
            )
            return
        if result.status.value != "SUCCEEDED":
            publish_payment_result(
                status="FAILED",
                correlation_id=message["correlationId"],
                previous_event_id=message["messageId"],
                order_id=payload["orderId"],
                transaction_id=result.transaction_id,
                provider=result.provider,
                amount=payload["amount"],
                currency=payload["currency"],
                reason_code="PAYMENT_DECLINED",
                message=result.reason or "Payment provider did not confirm the payment.",
            )
            return
        publish_payment_result(
            status=result.status.value,
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=result.transaction_id,
            provider=result.provider,
            amount=payload["amount"],
            currency=payload["currency"],
            customer=result.customer,
            shipping_address=result.shipping_address,
        )
        return

    # Erstattung als Saga-Kompensation.
    if message["type"] == "billing.refund.requested":
        payload = message.get("payload", {})
        provider = payload.get("provider") or settings.payment_provider
        facade = get_payment_facade(provider)
        try:
            result = facade.refund(
                payload["transactionId"],
                Decimal(payload["amount"]),
                correlation_id=message["correlationId"],
            )
        except PaymentFacadeError as exc:
            logger.error(
                "Refund failed",
                extra={
                    "correlation_id": message["correlationId"],
                    "context": {
                        "eventType": "billing.refund.failed",
                        "orderId": payload.get("orderId"),
                        "transactionId": payload.get("transactionId"),
                        "provider": provider,
                        "error": str(exc),
                    },
                },
            )
            event = build_message(
                "billing.refund.failed",
                message["correlationId"],
                {
                    "orderId": payload["orderId"],
                    "transactionId": payload.get("transactionId"),
                    "provider": provider,
                    "amount": payload["amount"],
                    "currency": payload["currency"],
                    "reasonCode": "REFUND_PROVIDER_ERROR",
                    "message": str(exc),
                },
                previous_event_id=message["messageId"],
            )
            publish_message("billing.refund.failed", event)
            return
        event = build_message(
            "billing.refund.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "transactionId": result.transaction_id,
                "provider": result.provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "refundStatus": result.status.value,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.refund.succeeded", event)
        return

    # Nicht gebundene Nachrichtentypen ignorieren.
    if message["type"] != "billing.payment.requested":
        return
    payload = message.get("payload", {})
    scenario = payload.get("scenario", "happy_path")
    # Konfigurierbare Fehlerszenarien ohne Anbieteraufruf.
    if scenario in {"payment_failed", "payment_timeout"}:
        reason_code = "PAYMENT_TIMEOUT" if scenario == "payment_timeout" else "PAYMENT_DECLINED"
        publish_payment_result(
            status="FAILED",
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=None,
            provider=payload.get("provider") or settings.payment_provider,
            amount=payload["amount"],
            currency=payload["currency"],
            scenario=scenario,
            reason_code=reason_code,
            message="Payment-Stub simuliert eine fehlgeschlagene Zahlung.",
        )
        return

    # Zahlung ueber den konfigurierten Anbieter.
    provider = payload.get("provider") or settings.payment_provider
    facade = get_payment_facade(provider)
    payment = dict(payload.get("payment", {}))
    payment["scenario"] = scenario
    payment["correlationId"] = message["correlationId"]
    payment["previousEventId"] = message["messageId"]
    try:
        result = facade.charge(
            payload["orderId"],
            Decimal(payload["amount"]),
            payload["currency"],
            payment.get("testPaymentMethod"),
            payment,
        )
    except PaymentFacadeError as exc:
        publish_payment_result(
            status="FAILED",
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=None,
            provider=provider,
            amount=payload["amount"],
            currency=payload["currency"],
            reason_code="PAYMENT_PROVIDER_ERROR",
            message=str(exc),
        )
        return
    # PENDING wartet auf Redirect-Bestaetigung oder Stub-Webhook.
    if result.status.value == "PENDING":
        event = build_message(
            "billing.payment.pending",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "transactionId": result.transaction_id,
                "provider": result.provider,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "paymentStatus": result.status.value,
                "redirectUrl": result.redirect_url,
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.pending", event)
        logger.info(
            "Payment confirmation pending",
            extra={
                "correlation_id": message["correlationId"],
                "context": {
                    "eventType": "billing.payment.pending",
                    "orderId": payload["orderId"],
                    "provider": result.provider,
                    "paymentStatus": result.status.value,
                },
            },
        )
        return
    if result.status.value != "SUCCEEDED":
        publish_payment_result(
            status="FAILED",
            correlation_id=message["correlationId"],
            previous_event_id=message["messageId"],
            order_id=payload["orderId"],
            transaction_id=result.transaction_id,
            provider=result.provider,
            amount=payload["amount"],
            currency=payload["currency"],
            reason_code="PAYMENT_DECLINED",
            message=result.reason or "Payment provider did not approve the payment.",
        )
        return
    publish_payment_result(
        status=result.status.value,
        correlation_id=message["correlationId"],
        previous_event_id=message["messageId"],
        order_id=payload["orderId"],
        transaction_id=result.transaction_id,
        provider=result.provider,
        amount=payload["amount"],
        currency=payload["currency"],
        scenario=scenario,
    )
