"""HTTP-Clients fuer Audit- und Warehouse-Service."""

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import uuid4

from fastapi import HTTPException

from .config import settings

logger = logging.getLogger(__name__)


def fetch_audit_snapshots(correlation_id: str) -> list[dict]:
    """Liest die Audit-Timeline per HTTP."""
    url = f"{settings.audit_service_url.rstrip('/')}/audit/orders/{correlation_id}"
    request = UrlRequest(url, headers={"X-Correlation-Id": correlation_id})
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Audit service unavailable: {exc}") from exc
    return body.get("snapshots", [])


def fetch_warehouse_stock(correlation_id: str | None = None) -> dict[str, dict]:
    """Liest den gesamten Lagerbestand per HTTP."""
    url = f"{settings.warehouse_service_url.rstrip('/')}/stock"
    request = UrlRequest(url, headers={"X-Correlation-Id": correlation_id or str(uuid4())})
    try:
        with urlopen(request, timeout=3) as response:
            stock_entries = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("Warehouse stock unavailable", extra={"context": {"error": str(exc)}})
        return {}
    return {entry["productId"]: entry for entry in stock_entries}


def update_warehouse_stock(product_id: str, stock: dict, correlation_id: str | None = None) -> dict:
    """Aktualisiert einen Lagerbestand per HTTP."""
    url = f"{settings.warehouse_service_url.rstrip('/')}/stock/{product_id}"
    request = UrlRequest(
        url,
        data=json.dumps(stock).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Correlation-Id": correlation_id or str(uuid4())},
        method="PATCH",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise HTTPException(status_code=exc.code, detail=f"Warehouse stock update failed: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Warehouse service unavailable: {exc}") from exc


def create_warehouse_stock(stock: dict, correlation_id: str | None = None) -> dict:
    """Legt einen Lagerbestand per HTTP an."""
    url = f"{settings.warehouse_service_url.rstrip('/')}/stock"
    request = UrlRequest(
        url,
        data=json.dumps(stock).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Correlation-Id": correlation_id or str(uuid4())},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") if exc.fp else str(exc)
        raise HTTPException(status_code=exc.code, detail=f"Warehouse stock creation failed: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Warehouse service unavailable: {exc}") from exc
