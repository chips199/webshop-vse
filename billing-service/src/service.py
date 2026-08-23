"""Service-Schicht des billing-service: Saga-Command-Handling und Payment-Logik.

Enthaelt die eigentliche Business-Logik, die sowohl vom RabbitMQ-Consumer
(handle_billing_message, siehe lifespan() in main.py) als auch von den
HTTP-Endpunkten (routes.py, fuer den Async-Webhook-Callback) genutzt wird.
Kennt HTTP genausowenig wie RabbitMQ-Verbindungsdetails - spricht nur ueber
messaging.py (Events publizieren) und payment/ (PaymentFacade) mit der
Aussenwelt.
"""

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
    """Baut aus einem PaymentResult-Ausgang das passende Saga-Event und publiziert es.

    Zentrale Stelle, die "SUCCEEDED" -> billing.payment.succeeded und jeden
    anderen Status -> billing.payment.failed uebersetzt, damit diese
    Payload-/Event-Type-Logik nicht an jeder Aufrufstelle (charge-,
    confirm- und Webhook-Pfad) dupliziert werden muss.
    """
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
        # Nur gesetzt, wenn der Anbieter (mit Sandbox-Credentials) echte
        # Kaeufer-/Adressdaten zurueckgeliefert hat (siehe PaymentResult in
        # payment/models.py) - shop-service uebernimmt diese dann in die
        # Order und ueberschreibt damit die Checkout-Formular-Eingaben.
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
    """Verarbeitet eine einzelne RabbitMQ-Command-Nachricht fuer billing-service.

    Wird von consume_messages() (messaging.py) fuer jede empfangene
    Nachricht aufgerufen. Kennt drei Command-Typen, die hier jeweils in
    einem eigenen if-Block behandelt werden (kein Dispatch-Dict, weil jeder
    Zweig einen spuerbar unterschiedlichen Ablauf hat):

      - billing.payment.confirm.requested: Nutzer ist vom Stripe-/PayPal-
        Sandbox-Redirect zurueckgekehrt, hier wird der tatsaechliche
        Zahlungsabschluss per get_status() (=Capture) geprueft.
      - billing.refund.requested: Saga-Kompensation, erstattet eine bereits
        erfolgreiche Zahlung.
      - billing.payment.requested (Default/sonst): urspruengliche
        Zahlungsanforderung, stoesst charge() an.

    Jeder Zweig endet mit `return`, damit kein nachfolgender Zweig versehentlich
    zusaetzlich ausgefuehrt wird.
    """
    if message["type"] == "billing.payment.confirm.requested":
        payload = message.get("payload", {})
        provider = payload.get("provider") or settings.payment_provider
        facade = get_payment_facade(provider)
        try:
            # get_status() fuehrt bei PayPal den eigentlichen capture_order()
            # aus - erst hier wird das Geld tatsaechlich eingezogen.
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

    # Kompensations-Command: eine zuvor erfolgreiche Zahlung soll rueckgaengig
    # gemacht werden (z.B. weil ein spaeterer Saga-Schritt fehlschlug).
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

    # Alles ausser den beiden oben behandelten Typen ignorieren (z.B. falls
    # die Queue-Bindings in messaging.py sich mal aendern und billing-service
    # kurzzeitig auch fremde Routing-Keys sieht).
    if message["type"] != "billing.payment.requested":
        return
    payload = message.get("payload", {})
    scenario = payload.get("scenario", "happy_path")
    # Test-/Demo-Szenarien: ohne echten Adapter-Aufruf sofort ein
    # fehlgeschlagenes Ergebnis simulieren (fuer Fehlerpfad-Tests der Saga,
    # z.B. ueber shop-service order.payment.scenario steuerbar).
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

    # Regulaerer Zahlungsversuch: der eigentliche charge()-Aufruf gegen den
    # konfigurierten Anbieter.
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
    # PENDING = Redirect-Fall (echte Sandbox-Credentials) oder PayPal-Stub:
    # noch KEIN Saga-Endergebnis. Stattdessen ein Zwischen-Event, das
    # shop-service in PAYMENT_ACTION_REQUIRED versetzt (Redirect-URL) bzw.
    # auf den spaeteren Webhook warten laesst.
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
