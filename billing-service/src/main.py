"""FastAPI-App und Saga-Command-Handler des billing-service.

Zwei Eintrittspunkte in diesen Service:
  1. HTTP-Endpunkte (health check, Zahlungsstatus-Abfrage, der interne
     PayPal-Stub-Webhook) - siehe die @app.*-Routen unten.
  2. RabbitMQ-Commands (billing.payment.requested/.confirm.requested,
     billing.refund.requested) - werden in einem Hintergrund-Thread
     konsumiert und an handle_billing_message() weitergereicht (siehe
     lifespan()).

Beide Wege sprechen ausschliesslich ueber die PaymentFacade
(payment/facade.py) mit dem eigentlichen Zahlungsanbieter.
"""

from contextlib import asynccontextmanager
from decimal import Decimal
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message
from .payment import PaymentFacadeError, get_payment_facade
from .problem_details import register_problem_handlers

configure_logging()
logger = logging.getLogger(__name__)
# Steuert den sauberen Shutdown des Consumer-Threads (siehe lifespan()).
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI-Lifespan-Hook: startet/stoppt den RabbitMQ-Consumer-Thread.

    Alles vor `yield` laeuft beim App-Start, alles danach beim Shutdown.
    Der Consumer laeuft in einem eigenen Daemon-Thread, damit er die
    FastAPI-Event-Loop nicht blockiert (consume_messages() ist synchroner,
    blockierender pika-Code). Beim Shutdown wird stop_consumer_event
    gesetzt und bis zu 3s auf ein sauberes Thread-Ende gewartet.
    """
    global consumer_thread
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(
            ["billing.payment.requested", "billing.payment.confirm.requested", "billing.refund.requested"],
            handle_billing_message,
            stop_consumer_event,
        ),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Billing command consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Billing Service API", version="0.1.0", lifespan=lifespan)
register_problem_handlers(app)
# CORS bewusst eng gehalten: nur das lokale Frontend darf browserseitig
# zugreifen. Im Gateway-Prinzip des Projekts spricht das Frontend billing-
# service aber ohnehin nie direkt an (nur ueber shop-service) - die Regel
# hier ist eher Verteidigung in der Tiefe als aktiv genutzter Pfad.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)


class HealthResponse(BaseModel):
    """Antwort von GET /health - fuer Docker-Healthchecks/Monitoring."""

    status: str = "ok"
    service: str


class PaymentStatusResponse(BaseModel):
    """Antwort von GET /payments/{transactionId}/status."""

    transactionId: str
    provider: str
    status: str


class AsyncPaymentWebhookRequest(BaseModel):
    """Body des internen PayPal-Stub-Webhooks (POST /webhooks/payment-stub).

    Wird ausschliesslich vom PayPalAdapter selbst geschickt (siehe
    _send_webhook() in payment/adapters.py), simuliert damit den
    asynchronen Webhook-Callback eines echten Zahlungsanbieters
    (Bonusaufgabe 4.4).
    """

    orderId: str
    transactionId: str
    provider: str = "paypal"
    amount: str
    currency: str = "EUR"
    status: str
    correlationId: str
    previousEventId: str | None = None
    reasonCode: str | None = None
    message: str | None = None
    scenario: str = "async_webhook"


class AsyncPaymentWebhookResponse(BaseModel):
    """Antwort von POST /webhooks/payment-stub (nur Bestaetigung des Empfangs)."""

    accepted: bool = True
    eventType: str


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Stellt sicher, dass jeder Request eine correlationId hat und sie zurueckgibt.

    Uebernimmt eine vom Aufrufer mitgeschickte "X-Correlation-Id", oder
    erzeugt eine neue, falls keine da ist. Die ID landet in
    request.state.correlation_id (von den Handlern unten fuer strukturiertes
    Logging genutzt) und wird als Response-Header zurueckgespiegelt, damit
    Client und Server dieselbe ID fuer denselben Vorgang sehen.
    """
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Einfacher Liveness-/Readiness-Check fuer Docker Compose/Monitoring."""
    return HealthResponse(service=settings.service_name)


@app.get("/payments/{transactionId}/status", response_model=PaymentStatusResponse)
async def get_payment_status(transactionId: str) -> PaymentStatusResponse:
    """Fragt synchron den aktuellen Status einer Transaktion ab (Debug-/Admin-Zweck).

    Nutzt denselben Adapter-Retry wie der Saga-Confirm-Pfad in
    handle_billing_message(); eine dauerhaft fehlschlagende Statusabfrage
    fuehrt hier (mangels eigenem try/except) zu einer unbehandelten
    PaymentFacadeError, die vom generischen Exception-Handler in
    problem_details.py als 500 beantwortet wird.
    """
    facade = get_payment_facade(settings.payment_provider)
    result = facade.get_status(transactionId)
    return PaymentStatusResponse(
        transactionId=result.transaction_id,
        provider=result.provider,
        status=result.status.value,
    )


@app.post("/webhooks/payment-stub", response_model=AsyncPaymentWebhookResponse)
async def receive_async_payment_webhook(request: AsyncPaymentWebhookRequest) -> AsyncPaymentWebhookResponse:
    """Empfaengt den asynchronen Webhook-Callback des PayPal-Stubs (Bonus 4.4).

    Wird ausschliesslich intern vom PayPalAdapter selbst aufgerufen (siehe
    _schedule_webhook()/_send_webhook() in payment/adapters.py), simuliert
    also den Callback, den ein echter Zahlungsanbieter nach verzoegerter
    Zahlungsbestaetigung schicken wuerde. Uebersetzt das Ergebnis in das
    passende billing.payment.succeeded/.failed-Saga-Event.
    """
    status = request.status.upper()
    if status not in {"SUCCEEDED", "FAILED"}:
        raise HTTPException(status_code=400, detail="Unsupported async payment webhook status")
    publish_payment_result(
        status=status,
        correlation_id=request.correlationId,
        previous_event_id=request.previousEventId,
        order_id=request.orderId,
        transaction_id=request.transactionId,
        provider=request.provider,
        amount=request.amount,
        currency=request.currency,
        scenario=request.scenario,
        reason_code=request.reasonCode,
        message=request.message,
    )
    event_type = "billing.payment.succeeded" if status == "SUCCEEDED" else "billing.payment.failed"
    return AsyncPaymentWebhookResponse(eventType=event_type)