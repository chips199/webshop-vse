"""FastAPI-Einstiegspunkt des audit-service.

audit-service ist bewusst ein generischer Event-Sink OHNE Business-Wissen
ueber Shop oder Zahlung (Aufgabenblatt 3.2): er bindet sich auf JEDE
Nachricht auf dem gemeinsamen Exchange (Routing-Key "#", siehe
messaging.py) und speichert sie unveraendert als Audit-Snapshot
(database.py). Fachlicher Code (main.py hier) besteht deshalb nur aus dem
Start des Consumers und dem einen Lese-Endpunkt fuer die Audit-Timeline
- es gibt keine Verzweigung nach Nachrichtentyp.
"""

from contextlib import asynccontextmanager
from datetime import datetime
import logging
import threading
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .database import get_snapshots_by_correlation_id, init_database, insert_snapshot_from_message
from .logging_config import configure_logging
from .messaging import consume_audit_events
from .problem_details import register_problem_handlers

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startet/stoppt den RabbitMQ-Consumer-Thread synchron mit der FastAPI-App.

    insert_snapshot_from_message wird direkt als Handler durchgereicht -
    anders als bei den anderen Services gibt es hier keine eigene
    "handle_message"-Zwischenschicht, weil audit-service jede Nachricht
    ohnehin 1:1 (ohne Fallunterscheidung) in einen Snapshot uebersetzt.
    """
    global consumer_thread
    init_database()
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_audit_events,
        args=(insert_snapshot_from_message, stop_consumer_event),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Audit event consumer started")
    yield
    # Sauberer Shutdown: stop_consumer_event signalisiert dem Consumer-Loop
    # in messaging.py, sich zu beenden; join() mit Timeout verhindert, dass
    # ein haengender Thread den App-Shutdown blockiert.
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Audit Service API", version="0.1.0", lifespan=lifespan)
register_problem_handlers(app)
# CORS: audit-service liefert keine Cookies/Sessions (allow_credentials=False),
# der Endpunkt wird ausschliesslich lesend genutzt (GET /audit/orders/{id}).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class AuditSnapshot(BaseModel):
    """Antwort-Schema fuer einen einzelnen Snapshot - Felder 1:1 wie in
    Aufgabenblatt 3.2 gefordert (correlationId, eventType, service,
    timestamp, payload, previousEventId, actor, statusCode)."""

    id: UUID
    correlationId: UUID
    eventType: str
    service: str
    timestamp: datetime
    payload: dict[str, Any]
    previousEventId: UUID | None = None
    actor: str
    statusCode: str


class AuditTimelineResponse(BaseModel):
    correlationId: UUID
    snapshots: list[AuditSnapshot]


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


@app.get("/audit/orders/{correlationId}", response_model=AuditTimelineResponse)
async def get_order_audit_timeline(correlationId: UUID) -> AuditTimelineResponse:
    """Der geforderte Audit-Endpunkt: alle Snapshots einer Bestellung,
    chronologisch sortiert (Sortierung passiert in der SQL-Query, siehe
    get_snapshots_by_correlation_id() in database.py)."""
    rows = list(get_snapshots_by_correlation_id(str(correlationId)))
    return AuditTimelineResponse(correlationId=correlationId, snapshots=rows)
