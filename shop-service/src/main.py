"""FastAPI-Anwendung des Shop-Service."""

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
    """Verwaltet Datenbank und RabbitMQ-Consumer waehrend der App-Laufzeit."""
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
register_problem_handlers(app)
# Statische Bereitstellung hochgeladener Produktbilder.
app.mount(
    "/product-images",
    StaticFiles(directory=settings.product_image_upload_dir, check_dir=False),
    name="uploaded-product-images",
)
# Browser-Zugriff des lokalen Frontends mit Admin-Session-Cookie.
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
    """Uebernimmt oder erzeugt die Korrelations-ID eines Requests."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


app.include_router(router)
