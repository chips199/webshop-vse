"""FastAPI-Entry-Point (Composition Root) des invoice-service.

Reine Zusammensetzung: erzeugt die FastAPI-App, verdrahtet Middleware,
Fehler-Handler und den RabbitMQ-Consumer-Thread, und bindet den HTTP-Router
ein. Business-Logik liegt in:

  - schemas.py: Pydantic-Request-/Response-Modelle
  - routes.py: GET-Endpunkte fuer Rechnungsmetadaten/-download (Router-Schicht)
  - service.py: Command-Handling ueber RabbitMQ (Saga) + Serialisierung
  - pdf.py: PDF-Rendering (kein externes Business-Wissen, reine Darstellung)
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
from .service import handle_invoice_message

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startet/stoppt den RabbitMQ-Consumer-Thread synchron mit der FastAPI-App.

    Der Consumer laeuft in einem eigenen daemon-Thread (nicht im asyncio-
    Event-Loop), weil consume_messages() blockierend/synchron ist (pika
    BlockingConnection).
    """
    global consumer_thread
    init_database()
    stop_consumer_event.clear()
    consumer_thread = threading.Thread(
        target=consume_messages,
        args=(["invoice.create.requested"], handle_invoice_message, stop_consumer_event),
        daemon=True,
    )
    consumer_thread.start()
    logger.info("Invoice command consumer started")
    yield
    # Sauberer Shutdown: stop_consumer_event signalisiert dem Consumer-Loop,
    # sich zu beenden; join() mit Timeout verhindert, dass ein haengender
    # Thread den App-Shutdown blockiert.
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Invoice Service API", version="0.1.0", lifespan=lifespan)
register_problem_handlers(app)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Liest X-Correlation-Id aus eingehenden Requests oder erzeugt eine neue,
    und haengt sie an die Response an."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


# Bindet die HTTP-Endpunkte aus routes.py ein (Router-Schicht).
app.include_router(router)
