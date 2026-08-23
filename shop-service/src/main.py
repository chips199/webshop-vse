"""FastAPI-Einstiegspunkt des shop-service.

Groesster und "zentralster" Service der Choreografie: legt Bestellungen an,
konsumiert praktisch alle Saga-Events der anderen Services (siehe
handle_saga_message()) und entscheidet dabei jeweils, welches naechste
Command/Event zu publizieren ist - OHNE dass es einen zentralen
Orchestrator gibt (jeder Service kennt nur seinen eigenen naechsten
Schritt). Enthaelt ausserdem den Produktkatalog-/Bestand-Proxy, das
Admin-Dashboard-Backend (Login, Bestelluebersicht, Produktverwaltung,
Echtzeit-Updates per SSE) und den Circuit Breaker fuer den
Invoice-Service-Aufruf (Bonusaufgabe 4.1).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import queue
import re
import secrets
import threading
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg.errors import UniqueViolation

from . import realtime
from .config import settings
from .database import calculate_total, create_admin_session, delete_admin_session
from .database import claim_payment_confirmation
from .database import create_order as create_order_record
from .database import create_product as create_product_record
from .database import enrich_items_from_products, get_admin_session, get_products
from .database import complete_order_if_ready
from .database import list_admin_orders, verify_admin_credentials
from .database import get_order as get_order_record
from .database import get_order_by_idempotency_key
from .database import init_database, update_order_status
from .database import update_product as update_product_record
from .database import update_invoice_created, update_payment_succeeded, update_warehouse_commit
from .database import update_payment_action_required
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message
from .problem_details import register_problem_handlers
from .resilience import CircuitBreaker, CircuitBreakerOpenError

configure_logging()
logger = logging.getLogger(__name__)
Path(settings.product_image_upload_dir).mkdir(parents=True, exist_ok=True)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None
invoice_circuit_breaker = CircuitBreaker(
    failure_threshold=settings.invoice_circuit_breaker_failure_threshold,
    reset_seconds=settings.invoice_circuit_breaker_reset_seconds,
    half_open_max_calls=settings.invoice_circuit_breaker_half_open_max_calls,
)


def handle_saga_message(message: dict) -> None:
    """Zentraler Verteiler fuer alle eingehenden Saga-Events (Choreografie).

    shop-service konsumiert praktisch jedes Event, das die anderen Services
    publizieren, und entscheidet hier - abhaengig vom message_type - was als
    naechstes zu tun ist (naechstes Command publizieren, Bestellstatus
    aktualisieren, oder beides). Jeder Zweig endet mit `return`, sobald er
    fertig ist. Reihenfolge der Zweige entspricht grob dem Happy-Path-Ablauf
    der Saga; Fehler-/Kompensationszweige (z.B. billing.payment.failed,
    warehouse.commit.failed) stossen jeweils die passende
    Rueckabwicklung an.
    """
    message_type = message["type"]
    payload = message.get("payload", {})
    correlation_id = message["correlationId"]

    # Admin-Dashboard-Echtzeit-Update (Bonusaufgabe 4.3): bewusst EIN
    # generischer Hook direkt hier statt eines eigenen Aufrufs in jedem der
    # vielen if-Zweige unten - fast jede Saga-Nachricht traegt eine orderId
    # im Payload, und dem Dashboard reicht "fuer diese Bestellung hat sich
    # etwas getan, bitte neu laden" (siehe realtime.py). Laeuft im
    # Consumer-Thread und darf daher nicht blockieren - notify_admin_dashboard
    # faengt eigene Fehler ab, damit ein Problem beim Publizieren die
    # eigentliche Saga-Verarbeitung niemals stoeren kann.
    notify_admin_dashboard(payload.get("orderId"), correlation_id, message_type)

    if message_type == "warehouse.reservation.succeeded":
        order_id = payload["orderId"]
        update_order_status(order_id, "RESERVED")
        payment_requested = build_message(
            "billing.payment.requested",
            correlation_id,
            {
                "orderId": order_id,
                "amount": payload["amount"],
                "currency": payload["currency"],
                "provider": payload["provider"],
                "scenario": payload.get("scenario", "happy_path"),
                "payment": payload.get("payment", {}),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.payment.requested", payment_requested)
        update_order_status(order_id, "PAYMENT_PENDING")
        return

    if message_type == "warehouse.reservation.failed":
        update_order_status(payload["orderId"], "OUT_OF_STOCK")
        return

    if message_type == "billing.payment.pending":
        redirect_url = payload.get("redirectUrl")
        if redirect_url:
            update_payment_action_required(payload["orderId"], payload["transactionId"], redirect_url)
        return

    if message_type == "billing.payment.succeeded":
        order_id = payload["orderId"]
        update_payment_succeeded(
            order_id,
            payload["transactionId"],
            customer=payload.get("customer"),
            shipping_address=payload.get("shippingAddress"),
        )
        request_invoice_with_circuit(order_id, correlation_id, payload, message)

        commit_requested = build_message(
            "warehouse.commit.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "scenario": payload.get("scenario", "happy_path"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.commit.requested", commit_requested)
        return

    if message_type == "billing.payment.failed":
        order_id = payload["orderId"]
        update_order_status(order_id, "PAYMENT_FAILED")
        cancel_requested = build_message(
            "warehouse.cancel.requested",
            correlation_id,
            {
                "orderId": order_id,
                "reasonCode": payload.get("reasonCode", "PAYMENT_FAILED"),
                "message": "Zahlung fehlgeschlagen, Warehouse-Reservierung wird storniert.",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.cancel.requested", cancel_requested)
        return

    if message_type == "warehouse.cancel.succeeded":
        update_order_status(payload["orderId"], "PAYMENT_FAILED")
        return

    if message_type == "invoice.created":
        order_id = payload["orderId"]
        transition = invoice_circuit_breaker.record_success()
        publish_invoice_circuit_transition(correlation_id, order_id, transition, message["messageId"])
        update_invoice_created(order_id, payload["invoiceId"])
        maybe_publish_order_completed(order_id, correlation_id, message)
        return

    if message_type == "invoice.failed":
        order_id = payload["orderId"]
        # attempt kommt von invoice-service durchgereicht (siehe dortiges
        # handle_invoice_message) - Default 1 nur zur Absicherung, falls eine
        # aeltere/fremde Nachricht ohne dieses Feld hereinkommt.
        attempt = payload.get("attempt", 1)
        transition = invoice_circuit_breaker.record_failure(payload.get("reasonCode", "INVOICE_FAILED"))
        publish_invoice_circuit_transition(correlation_id, order_id, transition, message["messageId"])

        if attempt < settings.invoice_max_retries:
            # Noch Versuche uebrig: Bestellung bleibt (sichtbar) im Retry-Zustand,
            # und die Shop-Saga plant selbst den naechsten Versuch (siehe
            # schedule_invoice_retry) - invoice-service haelt dafuer keinen
            # eigenen Zustand mehr.
            update_order_status(order_id, "INVOICE_RETRY_PENDING")
            schedule_invoice_retry(order_id, correlation_id, payload, message, attempt)
        else:
            # Alle Versuche verbraucht: endgueltiger, nicht mehr automatisch
            # wiederholter Fehlerzustand - bewusst von INVOICE_RETRY_PENDING
            # unterschieden, damit im Admin-Frontend erkennbar ist, dass hier
            # kein weiterer Versuch mehr folgt.
            update_order_status(order_id, "INVOICE_FAILED")
            logger.error(
                "Invoice creation failed permanently after exhausting retries",
                extra={
                    "correlation_id": correlation_id,
                    "context": {"orderId": order_id, "attempt": attempt, "maxAttempts": settings.invoice_max_retries},
                },
            )
        return

    if message_type == "warehouse.commit.succeeded":
        order_id = payload["orderId"]
        update_warehouse_commit(order_id, "SUCCEEDED")
        maybe_publish_order_completed(order_id, correlation_id, message)
        return

    if message_type == "warehouse.commit.failed":
        order_id = payload["orderId"]
        update_order_status(order_id, "REFUND_PENDING")
        refund_requested = build_message(
            "billing.refund.requested",
            correlation_id,
            {
                "orderId": order_id,
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "reasonCode": payload.get("reasonCode", "WAREHOUSE_COMMIT_FAILED"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("billing.refund.requested", refund_requested)
        return

    if message_type == "billing.refund.succeeded":
        order_id = payload["orderId"]
        update_order_status(order_id, "ROLLBACK_COMPLETED")
        rollback_completed = build_message(
            "order.rollback.completed",
            correlation_id,
            {
                "orderId": order_id,
                "status": "ROLLBACK_COMPLETED",
                "transactionId": payload["transactionId"],
            },
            previous_event_id=message["messageId"],
        )
        publish_message("order.rollback.completed", rollback_completed)
        return

    if message_type == "billing.refund.failed":
        update_order_status(payload["orderId"], "REFUND_FAILED")


def notify_admin_dashboard(order_id: str | None, correlation_id: str | None, reason: str) -> None:
    """Benachrichtigt alle verbundenen Admin-Dashboards ueber eine Aenderung.

    `reason` ist der ausloesende Nachrichtentyp (z.B. "billing.payment.
    succeeded") bzw. "order.created"/"order.payment-confirmation" fuer die
    beiden HTTP-Aufrufstellen unten - nur zu Debugging-/Logging-Zwecken im
    Frontend, keine fachliche Bedeutung fuer den Consumer der SSE-Verbindung
    (siehe admin_events() / realtime.py): das Dashboard reagiert einheitlich
    mit "betroffene Bestellung neu laden", unabhaengig vom genauen Grund.
    """
    if not order_id:
        return
    try:
        realtime.publish({"orderId": str(order_id), "correlationId": str(correlation_id), "reason": reason})
    except Exception:
        # Darf die eigentliche Verarbeitung (Saga-Consumer oder HTTP-Request)
        # niemals stoeren - das Live-Update ist ein Komfortfeature, kein
        # fachlich kritischer Pfad.
        logger.warning("Failed to publish admin dashboard realtime event", extra={"context": {"orderId": order_id}})


def maybe_publish_order_completed(order_id: str, correlation_id: str, previous_message: dict) -> None:
    """Prueft, ob alle drei Saga-Zweige (Zahlung/Rechnung/Lager) fertig sind,
    und publiziert order.completed nur dann - siehe complete_order_if_ready()
    in database.py fuer die eigentliche (atomare) Bedingung. Wird nach JEDEM
    der drei moeglichen Abschluss-Events aufgerufen (invoice.created,
    warehouse.commit.succeeded), da die Reihenfolge nicht feststeht."""
    if not complete_order_if_ready(order_id):
        return
    order_completed = build_message(
        "order.completed",
        correlation_id,
        {
            "orderId": order_id,
            "status": "COMPLETED",
        },
        previous_event_id=previous_message["messageId"],
    )
    publish_message("order.completed", order_completed)


def request_invoice_with_circuit(
    order_id: str,
    correlation_id: str,
    payload: dict,
    previous_message: dict,
    attempt: int = 1,
) -> None:
    """Fordert eine Rechnung an - Circuit-Breaker-gated, mit Versuchsnummer.

    `attempt` ist 1 beim allerersten Versuch (ausgeloest durch
    billing.payment.succeeded) und wird bei Wiederholungen von
    schedule_invoice_retry() hochgezaehlt durchgereicht. invoice-service
    bekommt den Wert im Payload mit, damit es ihn beim Melden eines
    Fehlschlags unveraendert zurueckspiegeln kann (siehe invoice.failed-Zweig
    oben) - so muss der Retry-Zaehler an keiner Stelle dauerhaft
    gespeichert werden.
    """
    try:
        transition = invoice_circuit_breaker.before_call()
        publish_invoice_circuit_transition(correlation_id, order_id, transition, previous_message["messageId"])
    except CircuitBreakerOpenError as exc:
        update_order_status(order_id, "INVOICE_FAILED")
        logger.warning(
            "Invoice request blocked by circuit breaker",
            extra={"correlation_id": correlation_id, "context": {"orderId": order_id, "error": str(exc)}},
        )
        return

    order = get_order_record(order_id) or {}
    invoice_payload = {
        "orderId": order_id,
        "transactionId": payload["transactionId"],
        "provider": payload["provider"],
        "amount": payload["amount"],
        "currency": payload["currency"],
        "scenario": payload.get("scenario", "happy_path"),
        "customer": order.get("customer") or {},
        "shippingAddress": order.get("shippingAddress") or {},
        "billingAddress": order.get("billingAddress"),
        "items": order.get("items") or [],
        "attempt": attempt,
    }
    invoice_requested = build_message(
        "invoice.create.requested",
        correlation_id,
        invoice_payload,
        previous_event_id=previous_message["messageId"],
    )
    publish_message("invoice.create.requested", invoice_requested)


def schedule_invoice_retry(
    order_id: str,
    correlation_id: str,
    payload: dict,
    previous_message: dict,
    attempt: int,
) -> None:
    """Plant nach kurzem Backoff einen weiteren Rechnungs-Versuch.

    Diese Retry-Orchestrierung sitzt bewusst hier in der Shop-Saga und nicht
    mehr in invoice-service: nur shop-service kennt den Zustand des
    Circuit Breakers fuer Invoice-Aufrufe (Bonusaufgabe 4.1) und damit, ob ein
    weiterer Versuch aktuell ueberhaupt sinnvoll ist. Der eigentliche
    Retry-Aufruf laeuft in einem eigenen Timer-Thread ab (wie z.B. auch der
    verzoegerte Webhook in billing-service), damit der Nachrichten-Consumer
    hier nicht blockiert wird.

    `payload` ist das Payload der invoice.failed-Nachricht und enthaelt
    bereits transactionId/provider/amount/currency/scenario - alles, was
    request_invoice_with_circuit() braucht (Kunden-/Lieferdaten werden dort
    ohnehin frisch per get_order_record() nachgeladen statt hier
    mitgeschleppt zu werden).
    """
    next_attempt = attempt + 1
    retry_event = build_message(
        "invoice.retry.scheduled",
        correlation_id,
        {
            "orderId": order_id,
            "transactionId": payload.get("transactionId"),
            "attempt": next_attempt,
            "maxAttempts": settings.invoice_max_retries,
            "reasonCode": payload.get("reasonCode", "INVOICE_RENDER_FAILED"),
            "message": payload.get("message"),
        },
        previous_event_id=previous_message["messageId"],
    )
    publish_message("invoice.retry.scheduled", retry_event)

    delay_seconds = settings.invoice_retry_backoff_seconds * attempt
    timer = threading.Timer(
        delay_seconds,
        request_invoice_with_circuit,
        kwargs={
            "order_id": order_id,
            "correlation_id": correlation_id,
            "payload": payload,
            "previous_message": retry_event,
            "attempt": next_attempt,
        },
    )
    timer.daemon = True
    timer.start()


def publish_invoice_circuit_transition(
    correlation_id: str,
    order_id: str | None,
    transition,
    previous_event_id: str,
) -> None:
    """Meldet eine CircuitTransition (falls vorhanden) als eigenes Event, damit
    Zustandswechsel des Invoice-Circuit-Breakers ueber die Audit-Snapshots
    nachvollziehbar sind. `transition` ist None, wenn before_call()/
    record_success()/record_failure() keinen Zustandswechsel ausgeloest
    haben - dann wird bewusst nichts publiziert."""
    if transition is None:
        return
    event = build_message(
        "invoice.circuit.state.changed",
        correlation_id,
        {
            "circuitName": "invoice-service",
            "orderId": order_id,
            "previousState": transition.previous_state.value,
            "state": transition.state.value,
            "failureCount": transition.failure_count,
            "reasonCode": transition.reason,
        },
        previous_event_id=previous_event_id,
    )
    publish_message("invoice.circuit.state.changed", event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startet/stoppt den RabbitMQ-Consumer im Gleichschritt mit der App.

    Bindet beim Start den Consumer-Thread an alle Routing-Keys, die fuer die
    Shop-Saga relevant sind (siehe handle_saga_message()). Beim Shutdown
    wird stop_consumer_event gesetzt und dem Thread bis zu 3s Zeit gegeben,
    sauber zu beenden.
    """
    global consumer_thread
    init_database()
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(
            [
                "warehouse.reservation.succeeded",
                "warehouse.reservation.failed",
                "billing.payment.pending",
                "billing.payment.succeeded",
                "billing.payment.failed",
                "billing.refund.succeeded",
                "billing.refund.failed",
                "invoice.created",
                "invoice.failed",
                "warehouse.commit.succeeded",
                "warehouse.commit.failed",
                "warehouse.cancel.succeeded",
            ],
            handle_saga_message,
            stop_consumer_event,
        ),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Shop saga consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Historical Computer Parts Shop API", version="0.1.0", lifespan=lifespan)
# Registriert die RFC-7807-konformen Fehler-Handler (siehe problem_details.py).
register_problem_handlers(app)
# Stellt hochgeladene Produktbilder (siehe admin_upload_product_image()) unter
# /product-images/... als statische Dateien bereit.
app.mount(
    "/product-images",
    StaticFiles(directory=settings.product_image_upload_dir, check_dir=False),
    name="uploaded-product-images",
)
# CORS: nur das lokale Frontend (Dev-Server) darf Cross-Origin-Requests
# stellen; allow_credentials=True ist noetig, damit das Admin-Session-Cookie
# bei Cross-Origin-Aufrufen mitgeschickt wird. expose_headers macht
# X-Correlation-Id im Browser (z.B. fuer Debugging) lesbar.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)

ADMIN_SESSION_COOKIE = "admin_session"


class HealthResponse(BaseModel):
    """Antwort des /health-Endpunkts (fuer Docker-Healthchecks/Monitoring)."""

    status: str = "ok"
    service: str


class OrderItem(BaseModel):
    """Eine rohe Bestellposition aus dem Checkout-Request (nur productId +
    Menge - Preise werden serverseitig nachgeschlagen, siehe
    enrich_items_from_products())."""

    productId: str
    quantity: int = Field(ge=1)


class Customer(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str | None = None


class Address(BaseModel):
    street: str = Field(min_length=1)
    houseNumber: str = Field(min_length=1)
    postalCode: str = Field(min_length=1)
    city: str = Field(min_length=1)
    country: str = Field(min_length=1)


class PaymentSelection(BaseModel):
    """Zahlungsauswahl aus dem Checkout - `scenario` steuert das im
    Aufgabenblatt geforderte gezielte Durchspielen von Testszenarien
    (happy_path, warehouse_commit_failed, out_of_stock, ...) end-to-end
    durch die ganze Saga."""

    provider: str
    currency: str = "EUR"
    scenario: str = "happy_path"
    mode: str = "sandbox"
    cardholder: str | None = None
    testPaymentMethod: str | None = None
    paypalEmail: str | None = None
    webhookStatus: str | None = None
    webhookReasonCode: str | None = None
    webhookMessage: str | None = None


class CreateOrderRequest(BaseModel):
    """Body fuer POST /orders (Checkout-Formular)."""

    customerId: str | None = None
    customer: Customer
    shippingAddress: Address
    billingAddress: Address | None = None
    items: list[OrderItem] = Field(min_length=1)
    payment: PaymentSelection


class ProductResponse(BaseModel):
    """Produkt inkl. optionaler Lagerbestand-Felder (None/UNKNOWN, falls
    warehouse-service beim Aufruf nicht erreichbar war, siehe
    fetch_warehouse_stock())."""

    id: str
    name: str
    year: str
    description: str
    price: str
    currency: str
    imageUrl: str
    imageAlt: str
    imageSource: str
    imageLicense: str
    imageCredit: str
    quantityOnHand: int | None = None
    reservedQuantity: int | None = None
    availableQuantity: int | None = None
    location: str | None = None
    stockStatus: str = "UNKNOWN"


class ProductUpdateRequest(BaseModel):
    """Body fuer PUT /admin/products/{productId}."""

    name: str = Field(min_length=1)
    year: str = Field(min_length=1)
    description: str = Field(min_length=1)
    price: str = Field(min_length=1)
    currency: str = "EUR"
    imageUrl: str = Field(min_length=1)
    imageAlt: str = Field(min_length=1)
    imageSource: str | None = ""
    imageLicense: str | None = ""
    imageCredit: str | None = ""


class ProductCreateRequest(ProductUpdateRequest):
    """Body fuer POST /admin/products - erweitert ProductUpdateRequest um die
    initialen Lagerbestand-Felder, die beim Anlegen zusaetzlich an
    warehouse-service durchgereicht werden (siehe admin_create_product())."""

    quantityOnHand: int = Field(default=0, ge=0)
    location: str | None = "RETRO-A1"


class StockUpdateRequest(BaseModel):
    """Body fuer PATCH /admin/products/{productId}/stock."""

    quantityOnHand: int = Field(ge=0)
    location: str | None = None


class OrderResponse(BaseModel):
    """Oeffentliche Sicht auf eine Bestellung (GET/POST /orders/...)."""

    orderId: str
    correlationId: str
    status: str
    amount: str | None = None
    currency: str | None = None
    transactionId: str | None = None
    paymentRedirectUrl: str | None = None
    customer: dict | None = None
    shippingAddress: dict | None = None


class PaymentConfirmationRequest(BaseModel):
    """Body fuer POST /orders/{orderId}/payment-confirmation (Kunde bestaetigt
    oder storniert eine externe Zahlung, z.B. nach PayPal-Redirect)."""

    outcome: Literal["approved", "cancelled"]


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminSessionResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class AdminOrderResponse(BaseModel):
    """Vollstaendige Bestellsicht fuer das Admin-Dashboard (mehr Felder als
    OrderResponse, u.a. items/payment/Zeitstempel fuer die Uebersichtstabelle)."""

    orderId: str
    correlationId: str
    status: str
    amount: str
    currency: str
    customer: dict | None = None
    shippingAddress: dict | None = None
    billingAddress: dict | None = None
    items: list[dict] = []
    payment: dict | None = None
    transactionId: str | None = None
    invoiceId: str | None = None
    invoiceStatus: str | None = None
    warehouseCommitStatus: str | None = None
    createdAt: datetime
    updatedAt: datetime


class AdminAuditResponse(BaseModel):
    """Antwort von GET /admin/orders/{orderId}/audit - die vollstaendige
    Audit-Snapshot-Timeline einer Bestellung ueber alle Services hinweg."""

    orderId: str
    snapshots: list[dict]


class ImageUploadResponse(BaseModel):
    imageUrl: str
    filename: str


def _token_hash(token: str) -> str:
    """Hasht ein Admin-Session-Token fuer den Datenbankvergleich - siehe
    create_admin_session()/get_admin_session() in database.py: gespeichert
    wird nie das Klartext-Token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_admin(request: Request) -> str:
    """FastAPI-Dependency: prueft das Admin-Session-Cookie und liefert den
    eingeloggten Benutzernamen, sonst 401. In allen /admin/*-Endpunkten per
    `Depends(require_admin)` eingebunden."""
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    session = get_admin_session(_token_hash(token))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    return session["username"]


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Liest X-Correlation-Id aus eingehenden Requests oder erzeugt eine neue,
    haengt sie an die Response an (Aufgabenblatt 3.3/9.3)."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(service=settings.service_name)


@app.get("/products", response_model=list[ProductResponse])
async def list_products(request: Request, response: Response) -> list[ProductResponse]:
    """Oeffentlicher Produktkatalog inkl. Live-Lagerbestand (No-Store-Cache,
    damit Verfuegbarkeitsstatus nie veraltet aus dem Browser-Cache kommt)."""
    response.headers["Cache-Control"] = "no-store"
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return [
        ProductResponse(**_serialize_product(product, stock_by_product_id.get(str(product["id"]))))
        for product in get_products()
    ]


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_order(request: Request, order: CreateOrderRequest) -> OrderResponse:
    """Startet die Saga: legt die Bestellung an (Status PENDING) und
    publiziert order.created sowie den ersten Saga-Schritt
    (warehouse.reserve.requested). 202 Accepted statt 201/200, da die
    Verarbeitung danach vollstaendig asynchron ueber RabbitMQ weiterlaeuft.

    idempotency_key/request_hash (Bonusaufgabe 4.2): ist ein Idempotency-Key-
    Header gesetzt und wurde er schon einmal fuer denselben Request-Body
    verwendet, wird die bereits angelegte Bestellung zurueckgegeben statt
    eine zweite anzulegen; bei gleichem Key aber anderem Body gibt es 409.
    """
    correlation_id = request.state.correlation_id
    idempotency_key = _idempotency_key_from_request(request)
    request_hash = _request_hash(order)
    if idempotency_key:
        existing_order = get_order_by_idempotency_key(idempotency_key)
        if existing_order:
            if existing_order.get("requestHash") != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key was already used for a different request body.",
                )
            return _initial_order_response(existing_order)

    order_id = str(uuid4())
    customer_id = order.customerId or str(uuid4())
    raw_items = [item.model_dump() for item in order.items]
    try:
        enriched_items = enrich_items_from_products(raw_items)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    amount = calculate_total(enriched_items)
    currency = order.payment.currency
    payment = order.payment.model_dump()
    try:
        create_order_record(
            order_id,
            correlation_id,
            customer_id,
            order.customer.model_dump(),
            order.shippingAddress.model_dump(),
            order.billingAddress.model_dump() if order.billingAddress else None,
            enriched_items,
            payment,
            str(amount),
            currency,
            idempotency_key,
            request_hash,
        )
    except UniqueViolation as exc:
        # Race Condition: zwei parallele Requests mit demselben Idempotency-
        # Key haben den Vorab-Check oben beide "nicht gefunden" gesehen und
        # versuchen nun gleichzeitig, die Bestellung anzulegen - der
        # eindeutige Index auf idempotency_key (siehe database.py) laesst
        # nur den ersten INSERT durch. Statt den Fehler durchzureichen, wird
        # hier die (vom parallelen Request inzwischen angelegte) Bestellung
        # nachgeladen und wie ein normaler Idempotenz-Treffer behandelt.
        if not idempotency_key:
            raise
        existing_order = get_order_by_idempotency_key(idempotency_key)
        if existing_order and existing_order.get("requestHash") == request_hash:
            return _initial_order_response(existing_order)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different request body.",
        ) from exc
    order_created = build_message(
        "order.created",
        correlation_id,
        {
            "orderId": order_id,
            "customerId": customer_id,
            "customer": order.customer.model_dump(),
            "shippingAddress": order.shippingAddress.model_dump(),
            "items": enriched_items,
            "payment": payment,
            "amount": str(amount),
            "currency": currency,
            "status": "PENDING",
        },
    )
    publish_message("order.created", order_created)
    notify_admin_dashboard(order_id, correlation_id, "order.created")

    reserve_requested = build_message(
        "warehouse.reserve.requested",
        correlation_id,
        {
            "orderId": order_id,
            "items": enriched_items,
            "amount": str(amount),
            "currency": currency,
            "provider": order.payment.provider,
            "scenario": order.payment.scenario,
            "payment": payment,
        },
        previous_event_id=order_created["messageId"],
    )
    publish_message("warehouse.reserve.requested", reserve_requested)

    logger.info(
        "Order accepted for asynchronous processing",
        extra={
            "correlation_id": correlation_id,
            "context": {"eventType": "order.accepted", "orderId": order_id, "itemCount": len(order.items)},
        },
    )
    return OrderResponse(
        orderId=order_id,
        correlationId=correlation_id,
        status="PENDING",
        amount=str(amount),
        currency=currency,
    )


@app.get("/orders/{orderId}", response_model=OrderResponse)
async def get_order(orderId: str) -> OrderResponse:
    """Liefert den aktuellen Saga-/Bestellstatus (Frontend pollt diesen
    Endpunkt waehrend der asynchronen Verarbeitung)."""
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    return _order_response(order)


@app.post("/orders/{orderId}/payment-confirmation", response_model=OrderResponse, status_code=status.HTTP_202_ACCEPTED)
async def confirm_order_payment(orderId: str, confirmation: PaymentConfirmationRequest) -> OrderResponse:
    """Kunde bestaetigt oder storniert eine ausstehende externe Zahlung
    (z.B. nach Rueckkehr von einer PayPal-Sandbox-Seite). Bei "approved"
    wird billing.payment.confirm.requested publiziert (billing-service
    fuehrt dann den eigentlichen Capture durch), bei "cancelled" direkt die
    Warehouse-Reservierung storniert."""
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    if order["status"] != "PAYMENT_ACTION_REQUIRED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order {orderId} is not awaiting a payment confirmation (status={order['status']}).",
        )
    # Claim ist die eigentliche (atomare) Absicherung gegen doppelte Aufrufe;
    # die Pruefung oben ist nur ein schneller Vorab-Check fuer den 404/409-Fall
    # ohne Beruehrung der Order. Zwischen diesem SELECT und dem claim liegt
    # zwar weiterhin ein Zeitfenster, aber das UPDATE...WHERE status=... in
    # claim_payment_confirmation() laesst bei einem parallelen Zweitaufruf
    # keinen zweiten Treffer zu - siehe Docstring dort.
    if not claim_payment_confirmation(orderId):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order {orderId} payment confirmation was already processed.",
        )
    correlation_id = str(order["correlationId"])
    notify_admin_dashboard(orderId, correlation_id, "order.payment-confirmation")

    if confirmation.outcome == "cancelled":
        update_order_status(orderId, "PAYMENT_FAILED")
        cancel_requested = build_message(
            "warehouse.cancel.requested",
            correlation_id,
            {
                "orderId": orderId,
                "reasonCode": "PAYMENT_CANCELLED",
                "message": "Kunde hat die Zahlung abgebrochen, Warehouse-Reservierung wird storniert.",
            },
        )
        publish_message("warehouse.cancel.requested", cancel_requested)
    else:
        confirm_requested = build_message(
            "billing.payment.confirm.requested",
            correlation_id,
            {
                "orderId": orderId,
                "transactionId": order.get("transactionId"),
                "provider": (order.get("payment") or {}).get("provider"),
                "amount": str(order["amount"]),
                "currency": order["currency"],
            },
        )
        publish_message("billing.payment.confirm.requested", confirm_requested)

    return _order_response(get_order_record(orderId))


def _idempotency_key_from_request(request: Request) -> str | None:
    """Liest und validiert den optionalen Idempotency-Key-Header (Bonusaufgabe
    4.2). None, falls der Header fehlt; 400, falls er leer oder zu lang ist."""
    value = request.headers.get("Idempotency-Key")
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Idempotency-Key header must not be empty")
    if len(stripped) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key header must not exceed 128 characters")
    return stripped


def _request_hash(order: CreateOrderRequest) -> str:
    """Bildet einen stabilen Hash ueber den Request-Body (sortierte Keys,
    kompakte Trennzeichen) - dient dazu, bei Wiederverwendung eines
    Idempotency-Keys zu erkennen, ob es sich um denselben oder einen
    fachlich abweichenden Request handelt (siehe create_order())."""
    canonical = json.dumps(order.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _order_response(order: dict) -> OrderResponse:
    """Serialisiert einen DB-Bestelldatensatz zur oeffentlichen OrderResponse."""
    return OrderResponse(
        orderId=str(order["orderId"]),
        correlationId=str(order["correlationId"]),
        status=order["status"],
        amount=str(order["amount"]),
        currency=order["currency"],
        transactionId=order.get("transactionId"),
        paymentRedirectUrl=order.get("paymentRedirectUrl"),
        customer=order.get("customer"),
        shippingAddress=order.get("shippingAddress"),
    )


def _initial_order_response(order: dict) -> OrderResponse:
    """Wie _order_response(), aber fuer den Idempotenz-Kurzschluss in
    create_order(): dort liegen nur wenige Felder vor (der DB-Datensatz aus
    get_order_by_idempotency_key() enthaelt nicht alle OrderResponse-Felder),
    daher der bewusst simplere Status "PENDING" statt des tatsaechlichen
    aktuellen Status."""
    return OrderResponse(
        orderId=str(order["orderId"]),
        correlationId=str(order["correlationId"]),
        status="PENDING",
        amount=str(order["amount"]),
        currency=order["currency"],
    )


@app.post("/admin/login", response_model=AdminSessionResponse)
async def admin_login(credentials: AdminLoginRequest, response: Response) -> AdminSessionResponse:
    """Prueft Admin-Zugangsdaten und setzt bei Erfolg ein httpOnly-
    Session-Cookie (siehe require_admin() fuer die Pruefung auf den
    folgenden Requests)."""
    if not verify_admin_credentials(credentials.username, credentials.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.admin_session_hours)
    create_admin_session(_token_hash(token), credentials.username, expires_at)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="lax",
        max_age=settings.admin_session_hours * 60 * 60,
        path="/",
    )
    return AdminSessionResponse(authenticated=True, username=credentials.username)


@app.post("/admin/logout", response_model=AdminSessionResponse)
async def admin_logout(request: Request, response: Response) -> AdminSessionResponse:
    """Loescht die Server-seitige Session sowie das Cookie."""
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token:
        delete_admin_session(_token_hash(token))
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return AdminSessionResponse(authenticated=False)


@app.get("/admin/session", response_model=AdminSessionResponse)
async def admin_session(username: str = Depends(require_admin)) -> AdminSessionResponse:
    """Erlaubt dem Frontend zu pruefen, ob eine bestehende Session noch
    gueltig ist (z.B. beim Neuladen der Admin-Seite)."""
    return AdminSessionResponse(authenticated=True, username=username)


@app.get("/admin/orders", response_model=list[AdminOrderResponse])
async def admin_orders(_: str = Depends(require_admin)) -> list[AdminOrderResponse]:
    """Bestelluebersicht fuer das Admin-Dashboard (neueste zuerst, siehe
    list_admin_orders())."""
    return [AdminOrderResponse(**_serialize_order(order)) for order in list_admin_orders()]


@app.get("/admin/orders/{orderId}/audit", response_model=AdminAuditResponse)
async def admin_order_audit(orderId: str, _: str = Depends(require_admin)) -> AdminAuditResponse:
    """Liefert die vollstaendige Audit-Snapshot-Timeline einer Bestellung
    ueber alle Services hinweg (per HTTP-Aufruf an audit-service, siehe
    fetch_audit_snapshots())."""
    order = get_order_record(orderId)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {orderId} not found")
    return AdminAuditResponse(
        orderId=orderId,
        snapshots=fetch_audit_snapshots(str(order["correlationId"])),
    )


@app.get("/admin/orders/events")
async def admin_orders_events(request: Request, _: str = Depends(require_admin)) -> StreamingResponse:
    """Server-Sent-Events-Stream fuer Echtzeit-Updates im Admin-Dashboard (Bonus 4.3).

    Statt bei jeder Aenderung den vollen Bestellzustand zu pushen (was hier
    doppelte Business-Logik ggue. den bestehenden REST-Endpunkten bedeuten
    wuerde), sendet dieser Stream nur ein minimales "orderId X hat sich
    veraendert"-Signal (siehe realtime.py/notify_admin_dashboard). Das
    Frontend reagiert darauf, indem es die Bestellliste bzw. die gerade
    geoeffnete Detailansicht per bestehendem REST-Aufruf neu laedt - einzige
    Quelle der Wahrheit bleibt die Datenbank, nicht der Event-Stream selbst.
    """

    async def event_stream():
        subscriber_queue = realtime.subscribe()
        try:
            # Direkt nach Verbindungsaufbau ein Kommentar-Event senden, damit
            # der Browser die SSE-Verbindung sofort als erfolgreich geoeffnet
            # erkennt (EventSource wertet erst eine "data:"-Zeile als Event,
            # ein Kommentar reicht aber schon, um die Verbindung "warm" zu
            # halten und Proxies/Load-Balancer nicht vorzeitig timeouten zu
            # lassen).
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # queue.Queue.get() ist blockierend (Thread-API) - daher
                    # im Executor ausfuehren, damit der asyncio-Event-Loop
                    # waehrenddessen andere Requests/Verbindungen bedienen
                    # kann. Der Timeout sorgt dafuer, dass request.is_
                    # disconnected() regelmaessig neu geprueft wird, auch
                    # wenn gerade keine Events ankommen.
                    event = await asyncio.get_running_loop().run_in_executor(
                        None, _get_with_timeout, subscriber_queue, 15.0
                    )
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            realtime.unsubscribe(subscriber_queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Defensiv: viele Reverse-Proxies (z.B. Nginx) puffern
            # Streaming-Responses standardmaessig, was SSE-Events nur
            # verzoegert oder gar nicht beim Browser ankommen liesse. Im
            # lokalen Docker-Compose-Aufbau ruft das Frontend shop-service
            # zwar direkt auf (kein Nginx davor, siehe frontend/nginx.conf),
            # der Header schadet aber nicht und macht den Endpunkt auch
            # hinter einem echten Reverse-Proxy sofort funktionsfaehig.
            "X-Accel-Buffering": "no",
        },
    )


def _get_with_timeout(q: "queue.Queue", timeout: float):
    """Kleiner Wrapper um Queue.get(), damit run_in_executor() sauber mit
    dem `timeout`-Keyword-Argument statt einer positional-only-Signatur
    umgehen kann."""
    return q.get(timeout=timeout)


@app.get("/admin/products", response_model=list[ProductResponse])
async def admin_products(request: Request, _: str = Depends(require_admin)) -> list[ProductResponse]:
    """Wie /products, aber hinter Admin-Login (Basis fuer die
    Produktverwaltung im Dashboard)."""
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return [
        ProductResponse(**_serialize_product(product, stock_by_product_id.get(str(product["id"]))))
        for product in get_products()
    ]


@app.post("/admin/product-images", response_model=ImageUploadResponse)
async def admin_upload_product_image(
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
) -> ImageUploadResponse:
    """Nimmt ein Produktbild entgegen, validiert Typ/Groesse und speichert es
    unter einem sicheren, kollisionsfreien Dateinamen im Upload-Verzeichnis.

    Der Dateiname wird NIE 1:1 vom Original-Upload uebernommen: der Stem wird
    auf a-z0-9/Bindestrich normalisiert (verhindert Path-Traversal/
    Sonderzeichen-Probleme) und um ein zufaelliges Suffix ergaenzt
    (verhindert, dass zwei Uploads mit gleichem Namen sich gegenseitig
    ueberschreiben).
    """
    content_types = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    if file.content_type not in content_types:
        raise HTTPException(status_code=415, detail="Only PNG, JPEG and WebP images are supported")
    content = await file.read()
    if len(content) > 6 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must not be larger than 6 MB")
    original_stem = Path(file.filename or "product-image").stem.lower()
    safe_stem = re.sub(r"[^a-z0-9]+", "-", original_stem).strip("-") or "product-image"
    filename = f"{safe_stem}-{uuid4().hex[:10]}{content_types[file.content_type]}"
    output_path = Path(settings.product_image_upload_dir) / filename
    output_path.write_bytes(content)
    return ImageUploadResponse(
        imageUrl=f"{settings.shop_public_base_url.rstrip('/')}/product-images/{filename}",
        filename=filename,
    )


@app.post("/admin/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_product(
    request: Request,
    product: ProductCreateRequest,
    _: str = Depends(require_admin),
) -> ProductResponse:
    """Legt ein neues Produkt UND (in einem zweiten Schritt) den zugehoerigen
    initialen Lagerbestand in warehouse-service an - zwei separate
    Datenbanken (product_id existiert erst nach dem ersten Aufruf), daher
    kein gemeinsamer Transaktionsrahmen."""
    product_id = str(uuid4())
    product_payload = product.model_dump(exclude={"quantityOnHand", "location"})
    created = create_product_record(product_id, product_payload)
    created_stock = create_warehouse_stock(
        {
            "productId": product_id,
            "quantityOnHand": product.quantityOnHand,
            "location": product.location or "RETRO-A1",
        },
        request.state.correlation_id,
    )
    return ProductResponse(**_serialize_product(created, created_stock))


@app.put("/admin/products/{productId}", response_model=ProductResponse)
async def admin_update_product(
    request: Request,
    productId: str,
    product: ProductUpdateRequest,
    _: str = Depends(require_admin),
) -> ProductResponse:
    """Aktualisiert die Stammdaten (Name/Preis/Bild/...) eines Produkts.
    Beruehrt NICHT den Lagerbestand - dafuer siehe admin_update_product_stock()."""
    updated = update_product_record(productId, product.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Product {productId} not found")
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return ProductResponse(**_serialize_product(updated, stock_by_product_id.get(str(updated["id"]))))


@app.patch("/admin/products/{productId}/stock", response_model=ProductResponse)
async def admin_update_product_stock(
    request: Request,
    productId: str,
    stock: StockUpdateRequest,
    _: str = Depends(require_admin),
) -> ProductResponse:
    """Aktualisiert nur den Lagerbestand (Menge/Lagerort) eines Produkts -
    delegiert an warehouse-service, das die eigentliche Bestandstabelle
    haelt (shop-service besitzt keine eigenen Bestandsdaten)."""
    update_warehouse_stock(productId, stock.model_dump(), request.state.correlation_id)
    product = next((entry for entry in get_products() if str(entry["id"]) == productId), None)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {productId} not found")
    stock_by_product_id = fetch_warehouse_stock(request.state.correlation_id)
    return ProductResponse(**_serialize_product(product, stock_by_product_id.get(productId)))


def _serialize_product(product: dict, stock: dict | None = None) -> dict:
    """Kombiniert Produkt-Stammdaten (aus shop-service-DB) mit Bestandsdaten
    (aus warehouse-service) zu einem ProductResponse-Dict. Ohne Bestandsdaten
    (warehouse-service nicht erreichbar) bleibt stockStatus "UNKNOWN" statt
    faelschlich "verfuegbar" oder "nicht verfuegbar" zu behaupten."""
    serialized = {
        **product,
        "id": str(product["id"]),
        "price": str(product["price"]),
    }
    if stock is None:
        return {**serialized, "stockStatus": "UNKNOWN"}
    available = int(stock["availableQuantity"])
    return {
        **serialized,
        "quantityOnHand": int(stock["quantityOnHand"]),
        "reservedQuantity": int(stock["reservedQuantity"]),
        "availableQuantity": available,
        "location": stock.get("location"),
        "stockStatus": "OUT_OF_STOCK" if available <= 0 else "AVAILABLE",
    }


def _serialize_order(order: dict) -> dict:
    """Wandelt DB-spezifische Typen (UUID-Objekte) der Admin-Bestellsicht in
    Strings um, wie sie AdminOrderResponse erwartet."""
    return {
        **order,
        "orderId": str(order["orderId"]),
        "correlationId": str(order["correlationId"]),
        "amount": str(order["amount"]),
        "invoiceId": str(order["invoiceId"]) if order.get("invoiceId") else None,
    }


def _serialize_snapshot(snapshot: dict) -> dict:
    """Waere das Gegenstueck zu _serialize_order() fuer Audit-Snapshots (UUID/
    Zeitstempel-Konvertierung). HINWEIS: aktuell ungenutzt, da
    admin_order_audit() die bereits fertig serialisierten Snapshots per
    fetch_audit_snapshots() (HTTP-JSON von audit-service) bezieht statt
    diese Funktion auf ein DB-Ergebnis anzuwenden."""
    return {
        **snapshot,
        "id": str(snapshot["id"]),
        "correlationId": str(snapshot["correlationId"]),
        "previousEventId": str(snapshot["previousEventId"]) if snapshot.get("previousEventId") else None,
        "timestamp": snapshot["timestamp"].isoformat(),
    }


def fetch_audit_snapshots(correlation_id: str) -> list[dict]:
    """Holt die Audit-Timeline synchron per HTTP von audit-service (statt
    direkt aus dessen Tabelle zu lesen, siehe get_audit_snapshots_for_order()
    in database.py). 502, falls audit-service nicht erreichbar ist."""
    url = f"{settings.audit_service_url.rstrip('/')}/audit/orders/{correlation_id}"
    request = UrlRequest(url, headers={"X-Correlation-Id": correlation_id})
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Audit service unavailable: {exc}") from exc
    return body.get("snapshots", [])


def fetch_warehouse_stock(correlation_id: str | None = None) -> dict[str, dict]:
    """Holt den aktuellen Lagerbestand aller Produkte vom Warehouse-Service.

    `correlation_id` sollte immer die correlationId der aufrufenden Anfrage
    sein (siehe correlation_id_middleware), damit sich der Aufruf im
    strukturierten Logging/Tracing der Bestellung zuordnen laesst - vorher
    wurde hier faelschlich bei jedem Aufruf eine neue, zufaellige uuid4()
    erzeugt, was die Trace-Kette zwischen shop-service und warehouse-service
    zerriss. Der Default None/Fallback auf eine neue uuid4() bleibt nur als
    Absicherung fuer Aufrufe ohne Request-Kontext bestehen.
    """
    url = f"{settings.warehouse_service_url.rstrip('/')}/stock"
    request = UrlRequest(url, headers={"X-Correlation-Id": correlation_id or str(uuid4())})
    try:
        with urlopen(request, timeout=3) as response:
            stock_entries = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("Warehouse stock unavailable", extra={"context": {"error": str(exc)}})
        return {}
    return {entry["productId"]: entry for entry in stock_entries}


def update_warehouse_stock(product_id: str, stock: dict, correlation_id: str | None = None) -> dict:
    """Aktualisiert den Lagerbestand eines Produkts im Warehouse-Service.

    Siehe fetch_warehouse_stock() zum Zweck von correlation_id.
    """
    url = f"{settings.warehouse_service_url.rstrip('/')}/stock/{product_id}"
    request = UrlRequest(
        url,
        data=json.dumps(stock).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Correlation-Id": correlation_id or str(uuid4())},
        method="PATCH",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise HTTPException(status_code=exc.code, detail=f"Warehouse stock update failed: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Warehouse service unavailable: {exc}") from exc


def create_warehouse_stock(stock: dict, correlation_id: str | None = None) -> dict:
    """Legt einen initialen Lagerbestand fuer ein neu angelegtes Produkt an.

    Siehe fetch_warehouse_stock() zum Zweck von correlation_id.
    """
    url = f"{settings.warehouse_service_url.rstrip('/')}/stock"
    request = UrlRequest(
        url,
        data=json.dumps(stock).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Correlation-Id": correlation_id or str(uuid4())},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise HTTPException(status_code=exc.code, detail=f"Warehouse stock creation failed: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Warehouse service unavailable: {exc}") from exc
