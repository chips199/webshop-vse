"""FastAPI-Entry-Point (Composition Root) des shop-service.

Groesster und "zentralster" Service der Choreografie: legt Bestellungen an,
konsumiert praktisch alle Saga-Events der anderen Services und entscheidet
dabei jeweils, welches naechste Command/Event zu publizieren ist - OHNE dass
es einen zentralen Orchestrator gibt (jeder Service kennt nur seinen eigenen
naechsten Schritt). Enthaelt ausserdem den Produktkatalog-/Bestand-Proxy, das
Admin-Dashboard-Backend (Login, Bestelluebersicht, Produktverwaltung,
Echtzeit-Updates per SSE) und den Circuit Breaker fuer den
Invoice-Service-Aufruf (Bonusaufgabe 4.1).

Diese Datei ist reine Zusammensetzung: erzeugt die FastAPI-App, verdrahtet
Middleware, Fehler-Handler, statische Dateien und den RabbitMQ-Consumer-
Thread, und bindet den HTTP-Router ein. Business-Logik liegt in:

  - schemas.py: Pydantic-Request-/Response-Modelle
  - routes.py: alle HTTP-Endpunkte (Router-Schicht)
  - service.py: Router-Hilfsfunktionen (Admin-Session, Idempotenz, Serialisierung)
  - saga.py: Saga-Entscheidungslogik ueber RabbitMQ (Choreografie) + Circuit Breaker
  - clients.py: HTTP-Aufrufe an warehouse-/audit-service
  - database.py: Datenbankzugriff (Repository-Schicht)
  - messaging.py: RabbitMQ-Anbindung (Infrastruktur-Schicht)
"""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import threading
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_database
from .logging_config import configure_logging
from .messaging import consume_messages
from .problem_details import register_problem_handlers
from .routes import router
from .saga import handle_saga_message

configure_logging()
logger = logging.getLogger(__name__)
Path(settings.product_image_upload_dir).mkdir(parents=True, exist_ok=True)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startet/stoppt den RabbitMQ-Consumer im Gleichschritt mit der App.

    Bindet beim Start den Consumer-Thread an alle Routing-Keys, die fuer die
    Shop-Saga relevant sind (siehe handle_saga_message() in saga.py). Beim
    Shutdown wird stop_consumer_event gesetzt und dem Thread bis zu 3s Zeit
    gegeben, sauber zu beenden.
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
# Stellt hochgeladene Produktbilder (siehe admin_upload_product_image() in
# routes.py) unter /product-images/... als statische Dateien bereit.
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
