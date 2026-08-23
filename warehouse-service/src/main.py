"""FastAPI-Entry-Point (Composition Root) des warehouse-service.

Reine Zusammensetzung: erzeugt die FastAPI-App, verdrahtet Middleware,
Fehler-Handler und den RabbitMQ-Consumer-Thread, und bindet den HTTP-Router
ein. Business-Logik liegt in:

  - schemas.py: Pydantic-Request-/Response-Modelle
  - routes.py: synchrone REST-Endpunkte fuer Lagerbestand (Router-Schicht)
  - service.py: Bestand-Command-Handling ueber RabbitMQ (Saga)
  - database.py: Datenbankzugriff (Repository-Schicht)
  - messaging.py: RabbitMQ-Anbindung (Infrastruktur-Schicht)
"""

from contextlib import asynccontextmanager
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from .database import init_database
from .logging_config import configure_logging
from .messaging import consume_messages
from .problem_details import register_problem_handlers
from .routes import router
from .service import handle_warehouse_message

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


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


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Liest X-Correlation-Id aus eingehenden Requests oder erzeugt eine neue,
    haengt sie an die Response an (Aufgabenblatt 3.3/9.3)."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


# Bindet alle HTTP-Endpunkte aus routes.py ein (Router-Schicht).
app.include_router(router)
