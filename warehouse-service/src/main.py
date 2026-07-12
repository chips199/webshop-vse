from contextlib import asynccontextmanager
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

from .config import settings
from .logging_config import configure_logging
from .messaging import build_message, consume_messages, publish_message
from .problem_details import register_problem_handlers

configure_logging()
logger = logging.getLogger(__name__)
stop_consumer_event = threading.Event()
consumer_thread: threading.Thread | None = None


def handle_warehouse_message(message: dict) -> None:
    if message["type"] == "warehouse.cancel.requested":
        payload = message.get("payload", {})
        event = build_message(
            "warehouse.cancel.succeeded",
            message["correlationId"],
            {
                "orderId": payload["orderId"],
                "cancelStatus": "SUCCEEDED",
                "reasonCode": payload.get("reasonCode", "CANCEL_REQUESTED"),
            },
            previous_event_id=message["messageId"],
        )
        publish_message("warehouse.cancel.succeeded", event)
        return

    if message["type"] == "warehouse.commit.requested":
        payload = message.get("payload", {})
        scenario = payload.get("scenario", "happy_path")
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
    has_stock = scenario != "out_of_stock" and all(int(item.get("quantity", 0)) > 0 for item in items)

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
            "reasonCode": "OUT_OF_STOCK",
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
    global consumer_thread
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
register_problem_handlers(app)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


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
