"""FastAPI-Entry-Point (Composition Root) des audit-service.

Reine Zusammensetzung: erzeugt die FastAPI-App, verdrahtet Middleware,
Fehler-Handler und den RabbitMQ-Consumer-Thread, und bindet den HTTP-Router
ein. Business-Logik gibt es hier nicht: audit-service ist ein generischer
Event-Sink ohne Business-Wissen ueber Shop
oder Zahlung: er bindet sich auf JEDE Nachricht auf dem gemeinsamen Exchange
(Routing-Key "#", siehe messaging.py) und speichert sie unveraendert als
Audit-Snapshot (insert_snapshot_from_message() in database.py, der
Repository-Schicht). Die HTTP-Seite (routes.py) liest ebenfalls direkt aus
der Repository-Schicht - eine eigene Service-Schicht wuerde hier nur
unnoetig delegieren.
"""

from contextlib import asynccontextmanager
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .database import init_database, insert_snapshot_from_message
from .logging_config import configure_logging
from .messaging import consume_audit_events
from .problem_details import register_problem_handlers
from .routes import router

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


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Liest X-Correlation-Id aus eingehenden Requests oder erzeugt eine neue,
    und haengt sie an die Response an."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


# Bindet die beiden HTTP-Endpunkte aus routes.py ein (Router-Schicht).
app.include_router(router)
