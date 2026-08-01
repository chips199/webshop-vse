import json
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


class JsonFormatter(logging.Formatter):
    """Formatiert jeden Log-Eintrag als einzeiliges JSON-Objekt.

    Damit sind Logs strukturiert (statt Freitext) und koennen von einem
    zentralen Log-Stack (Loki/Grafana, siehe docs/log-management.md) direkt
    geparst und nach Feldern wie correlationId oder orderId gefiltert werden.
    """

    def format(self, record: logging.LogRecord) -> str:
        # "context" und "correlation_id" sind keine Standard-LogRecord-Felder,
        # sondern werden von den Call-Sites ueber logger.info(..., extra={...})
        # mitgegeben (siehe z.B. main.py). getattr() mit Default schuetzt vor
        # Log-Aufrufen ohne diese Extras.
        context = getattr(record, "context", {})
        payload: dict[str, Any] = {
            "service": settings.service_name,
            "level": record.levelname,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": record.getMessage(),
            "correlationId": getattr(record, "correlation_id", None),
            "context": context,
        }
        # Fachliche Zusatzfelder aus "context" zusaetzlich auf der obersten
        # JSON-Ebene duplizieren (sofern der Schluessel noch frei ist) -
        # erleichtert das Filtern/Gruppieren im Log-Dashboard, ohne dass man
        # dort erst in ein verschachteltes "context"-Objekt greifen muss.
        if isinstance(context, dict):
            payload.update({key: value for key, value in context.items() if key not in payload})
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Richtet das Root-Logging fuer den gesamten Service einmalig ein.

    Wird beim Start von main.py aufgerufen, bevor irgendein Logger benutzt
    wird. Schreibt strukturierte JSON-Logs sowohl auf stdout (fuer
    docker logs / den zentralen Log-Stack) als auch in eine taeglich
    rotierende Datei unter ./logs (14 Tage Aufbewahrung).
    """
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

    # Vorhandene Handler entfernen, damit configure_logging() nicht bei
    # mehrfachem Aufruf (z.B. in Tests) doppelte Log-Zeilen erzeugt.
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)
    # pika (RabbitMQ-Client) loggt auf INFO-Level sehr geschwaetzig
    # (Verbindungsaufbau etc.) - auf WARNING gedrosselt, damit die eigenen
    # fachlichen Logs nicht untergehen.
    logging.getLogger("pika").setLevel(logging.WARNING)
