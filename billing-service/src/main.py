"""FastAPI-Entry-Point (Composition Root) des billing-service.

Reine Zusammensetzung: erzeugt die FastAPI-App, verdrahtet Middleware,
Fehler-Handler und den RabbitMQ-Consumer-Thread, und bindet den HTTP-Router
ein. Enthaelt keine Business-Logik mehr - die liegt in:

  - schemas.py: Pydantic-Request-/Response-Modelle
  - routes.py: HTTP-Endpunkte (Router-Schicht)
  - service.py: Saga-Command-Handling und Payment-Ergebnis-Uebersetzung
  - payment/: Payment-Fassade + Adapter (Strategy-Pattern)
  - messaging.py: RabbitMQ-Anbindung (Infrastruktur-Schicht)

Zwei Eintrittspunkte laufen hier zusammen:
  1. HTTP-Endpunkte (routes.py) - siehe include_router() unten.
  2. RabbitMQ-Commands (billing.payment.requested/.confirm.requested,
     billing.refund.requested) - werden in einem Hintergrund-Thread
     konsumiert und an service.handle_billing_message() weitergereicht
     (siehe lifespan()).
"""

from contextlib import asynccontextmanager
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .logging_config import configure_logging
from .messaging import consume_messages
from .problem_details import register_problem_handlers
from .routes import router
from .service import handle_billing_message

configure_logging()
logger = logging.getLogger(__name__)
# Steuert den sauberen Shutdown des Consumer-Threads (siehe lifespan()).
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


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
# CORS bewusst eng gehalten: nur das (per Konfiguration erlaubte) Frontend
# darf browserseitig zugreifen - Origins kommen aus settings.cors_allowed_
# origins statt hartcodiert zu sein, damit ein Deployment mit anderer
# Frontend-URL keine Code-Aenderung braucht. Im Gateway-Prinzip des
# Projekts spricht das Frontend billing-service aber ohnehin nie direkt an
# (nur ueber shop-service) - die Regel hier ist eher Verteidigung in der
# Tiefe als aktiv genutzter Pfad.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Stellt sicher, dass jeder Request eine correlationId hat und sie zurueckgibt.

    Uebernimmt eine vom Aufrufer mitgeschickte "X-Correlation-Id", oder
    erzeugt eine neue, falls keine da ist. Die ID landet in
    request.state.correlation_id (von den Handlern in routes.py fuer
    strukturiertes Logging genutzt) und wird als Response-Header
    zurueckgespiegelt, damit Client und Server dieselbe ID fuer denselben
    Vorgang sehen.
    """
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


# Bindet alle HTTP-Endpunkte aus routes.py ein (Router-Schicht).
app.include_router(router)
