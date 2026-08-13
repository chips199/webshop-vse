"""FastAPI-Einstiegspunkt des warehouse-service.

Konsumiert die drei Bestand-Commands von RabbitMQ (reserve/commit/cancel,
siehe handle_warehouse_message()) und stellt zusaetzlich synchrone
REST-Endpunkte fuer Lagerbestand (GET/POST/PATCH /stock) bereit, die z.B.
vom Shop-Service fuer die Produktkatalog-Anzeige und vom Admin-Dashboard
fuer die Bestandspflege genutzt werden.
"""

from contextlib import asynccontextmanager
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .config import settings
from .database import cancel_reservation, commit_reservation, create_stock, init_database, list_stock, reserve_stock, update_stock
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message
from .problem_details import register_problem_handlers

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


def handle_warehouse_message(message: dict) -> None:
    """Verteilt eingehende Bestand-Commands auf die passende Verarbeitung.

    Drei Nachrichtentypen (siehe Aufgabenblatt 2.2/2.3):
      - warehouse.cancel.requested: Reservierung stornieren (Kompensation
        bei abgelehnter Zahlung).
      - warehouse.commit.requested: Reservierung final ausbuchen (nach
        erfolgreicher Zahlung) - inkl. gezieltem Testszenario
        "warehouse_commit_failed" fuer die Refund-Kompensation.
      - warehouse.reserve.requested: Bestand pruefen und reservieren -
        inkl. gezieltem Testszenario "out_of_stock".
    Jeder Zweig publiziert am Ende genau EIN Ergebnis-Event zurueck an die
    Shop-Saga.
    """
    if message["type"] == "warehouse.cancel.requested":
        payload = message.get("payload", {})
        cancelled = cancel_reservation(payload["orderId"])
        event = build_message(
            "warehouse.cancel.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "cancelStatus": "SUCCEEDED" if cancelled else "SKIPPED",
                "reasonCode": payload.get("reasonCode", "CANCEL_REQUESTED"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.cancel.succeeded", event)
        return

    if message["type"] == "warehouse.commit.requested":
        payload = message.get("payload", {})
        scenario = payload.get("scenario", "happy_path")
        # Gezieltes Testszenario: tut so, als wuerde das finale Verbuchen der
        # Reservierung fehlschlagen, OHNE commit_reservation() ueberhaupt
        # aufzurufen - damit laesst sich die Refund-Kompensation der Saga
        # (Zahlung wurde bereits bestaetigt) reproduzierbar durchspielen.
        if scenario == "warehouse_commit_failed":
            event = build_message(
                "warehouse.commit.failed",
                message["correlationId"],
                {
                    "orderId": payload["orderId"],
                    "transactionId": payload["transactionId"],
                    "provider": payload["provider"],
                    "amount": payload["amount"],
                    "currency": payload["currency"],
                    "reasonCode": "WAREHOUSE_COMMIT_FAILED",
                    "message": "Reservierung konnte nach erfolgreicher Zahlung nicht final verbucht werden.",
                },
                previous_event_id=message["messageId"],
            )
            publish_message("warehouse.commit.failed", event)
            return

        committed = commit_reservation(payload["orderId"])
        if not committed:
            event = build_message(
                "warehouse.commit.failed",
                message["correlationId"],
                {
                    "orderId": payload["orderId"],
                    "transactionId": payload["transactionId"],
                    "provider": payload["provider"],
                    "amount": payload["amount"],
                    "currency": payload["currency"],
                    "reasonCode": "WAREHOUSE_COMMIT_FAILED",
                    "message": "Reservierung wurde nicht gefunden oder ist nicht mehr commitbar.",
                },
                previous_event_id=message["messageId"],
            )
            publish_message("warehouse.commit.failed", event)
            return

        event = build_message(
            "warehouse.commit.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "transactionId": payload["transactionId"],
                "provider": payload["provider"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "commitStatus": "SUCCEEDED",
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.commit.succeeded", event)
        return

    if message["type"] != "warehouse.reserve.requested":
        return
    payload = message.get("payload", {})
    order_id = payload["orderId"]
    items = payload.get("items", [])
    scenario = payload.get("scenario", "happy_path")
    has_stock, reason_code = reserve_stock(order_id, items)
    if scenario == "out_of_stock":
        # Gezieltes Testszenario: reserve_stock() lief regulaer (und hat
        # ggf. bereits reserviert), wird hier aber erzwungen wieder
        # storniert und als fehlgeschlagen gemeldet - reproduzierbarer
        # Weg, den "Artikel nicht verfuegbar"-Pfad der Saga zu testen, ohne
        # den tatsaechlichen Lagerbestand vorher leerraeumen zu muessen.
        cancel_reservation(order_id)
        has_stock = False
        reason_code = "OUT_OF_STOCK"

    if has_stock:
        event_type = "warehouse.reservation.succeeded"
        event_payload = {
            "orderId": order_id,
            "reservationId": f"reservation-{order_id}",
            "items": items,
            "amount": payload["amount"],
            "currency": payload["currency"],
            "provider": payload["provider"],
            "scenario": scenario,
            "payment": payload.get("payment", {}),
        }
    else:
        event_type = "warehouse.reservation.failed"
        event_payload = {
            "orderId": order_id,
            "reasonCode": reason_code or "OUT_OF_STOCK",
            "message": "Mindestens ein historisches Computerteil ist nicht verfuegbar.",
            "items": items,
        }

    event = build_message(
        event_type,
        message["correlationId"],
        event_payload,
        previous_event_id=message["messageId"],
    )
    publish_message(event_type, event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startet/stoppt den RabbitMQ-Consumer im Gleichschritt mit der App.

    Beim Start: Datenbank initialisieren, dann den Consumer-Thread fuer die
    drei Bestand-Commands starten (daemon=True, damit er den Prozess nicht
    am Beenden hindert). Beim Shutdown: stop_consumer_event setzen und dem
    Thread bis zu 3s Zeit geben, die laufende Verarbeitung sauber zu
    beenden.
    """
    global consumer_thread
    init_database()
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(
            ["warehouse.reserve.requested", "warehouse.commit.requested", "warehouse.cancel.requested"],
            handle_warehouse_message,
            stop_consumer_event,
        ),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Warehouse command consumer started")
    yield
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Warehouse Service API", version="0.1.0", lifespan=lifespan)
# Registriert die RFC-7807-konformen Fehler-Handler (siehe problem_details.py),
# damit Validierungs-/HTTP-Fehler als "application/problem+json" ausgeliefert
# werden statt im FastAPI-Standardformat.
register_problem_handlers(app)


class HealthResponse(BaseModel):
    """Antwort des /health-Endpunkts (fuer Docker-Healthchecks/Monitoring)."""

    status: str = "ok"
    service: str


class StockResponse(BaseModel):
    """Bestandsdatensatz eines Produkts, wie er nach aussen (REST) sichtbar ist."""

    productId: str
    quantityOnHand: int
    reservedQuantity: int
    availableQuantity: int
    location: str


class StockUpdateRequest(BaseModel):
    """Body fuer PATCH /stock/{productId} (Admin-Bestandspflege)."""

    quantityOnHand: int = Field(ge=0)
    location: str | None = None


class StockCreateRequest(StockUpdateRequest):
    """Body fuer POST /stock (neues Produkt im Lager anlegen)."""

    productId: str


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


@app.get("/stock", response_model=list[StockResponse])
async def stock() -> list[StockResponse]:
    """Liefert den kompletten Lagerbestand (z.B. fuer Produktkatalog/Admin-Dashboard)."""
    return [
        StockResponse(
            productId=str(entry["productId"]),
            quantityOnHand=entry["quantityOnHand"],
            reservedQuantity=entry["reservedQuantity"],
            availableQuantity=entry["availableQuantity"],
            location=entry["location"],
        )
        for entry in list_stock()
    ]


@app.post("/stock", response_model=StockResponse)
async def post_stock(request: StockCreateRequest) -> StockResponse:
    """Legt einen neuen Lagerbestand-Eintrag an oder aktualisiert ihn
    (create_stock() ist idempotent via ON CONFLICT, siehe database.py)."""
    created = create_stock(request.productId, request.quantityOnHand, request.location)
    return StockResponse(
        productId=str(created["productId"]),
        quantityOnHand=created["quantityOnHand"],
        reservedQuantity=created["reservedQuantity"],
        availableQuantity=created["availableQuantity"],
        location=created["location"],
    )


@app.patch("/stock/{productId}", response_model=StockResponse)
async def patch_stock(productId: str, request: StockUpdateRequest) -> StockResponse:
    """Aktualisiert quantityOnHand/location eines bestehenden Produkts (Admin-Pflege).

    409, falls die neue Menge unter die bereits reservierte Menge faellt
    (siehe ValueError in update_stock()); 404, falls das Produkt nicht existiert.
    """
    try:
        updated = update_stock(productId, request.quantityOnHand, request.location)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Stock entry for product {productId} not found")
    return StockResponse(
        productId=str(updated["productId"]),
        quantityOnHand=updated["quantityOnHand"],
        reservedQuantity=updated["reservedQuantity"],
        availableQuantity=updated["availableQuantity"],
        location=updated["location"],
    )
