import json
import logging
from datetime import datetime, timezone
from typing import Any

from .config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "service": settings.service_name,
            "level": record.levelname,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": record.getMessage(),
            "correlationId": getattr(record, "correlation_id", None),
            "context": getattr(record, "context", {}),
        }
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("pika").setLevel(logging.WARNING)
