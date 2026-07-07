from contextlib import asynccontextmanager
from datetime import datetime
import logging
import threading
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

from .config import settings
from .database import get_snapshots_by_correlation_id, init_database, insert_snapshot_from_message
from .logging_config import configure_logging
from .messaging import consume_audit_events

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
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


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class AuditSnapshot(BaseModel):
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
    rows = list(get_snapshots_by_correlation_id(str(correlationId)))
    return AuditTimelineResponse(correlationId=correlationId, snapshots=rows)
