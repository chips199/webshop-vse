import json
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
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
    formatter = JsonFormatter()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_dir / f"{settings.service_name}.log",
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)
    logging.getLogger("pika").setLevel(logging.WARNING)
