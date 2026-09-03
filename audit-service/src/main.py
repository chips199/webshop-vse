"""FastAPI-Anwendung des Audit-Service."""

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
    """Verwaltet Datenbank und RabbitMQ-Consumer waehrend der App-Laufzeit."""
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
    stop_consumer_event.set()
    if consumer_thread:
        consumer_thread.join(timeout=3)


app = FastAPI(title="Audit Service API", version="0.1.0", lifespan=lifespan)
register_problem_handlers(app)
# Zugriff des lokalen Frontends auf die Audit-Endpunkte.
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
    """Uebernimmt oder erzeugt die Korrelations-ID eines Requests."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


app.include_router(router)
