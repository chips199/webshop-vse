"""HTTP-Clients (Infrastruktur-Schicht) fuer die anderen Services.

shop-service besitzt weder eigene Bestandsdaten (die liegen in
warehouse-service) noch eigene Audit-Snapshots (die liegen in
audit-service) - beides wird stattdessen synchron per HTTP nachgeladen.
Bewusst mit der Python-Standardbibliothek (urllib) statt einer zusaetzlichen
HTTP-Client-Abhaengigkeit, da hier nur wenige, einfache Aufrufe noetig sind.
"""

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
    """Holt die Audit-Timeline synchron per HTTP von audit-service (statt
    direkt aus dessen Tabelle zu lesen, siehe get_audit_snapshots_for_order()
    in database.py). 502, falls audit-service nicht erreichbar ist."""
    url = f"{settings.audit_service_url.rstrip('/')}/audit/orders/{correlation_id}"
    request = UrlRequest(url, headers={"X-Correlation-Id": correlation_id})
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Audit service unavailable: {exc}") from exc
    return body.get("snapshots", [])


def fetch_warehouse_stock(correlation_id: str | None = None) -> dict[str, dict]:
    """Holt den aktuellen Lagerbestand aller Produkte vom Warehouse-Service.

    `correlation_id` sollte immer die correlationId der aufrufenden Anfrage
    sein (siehe correlation_id_middleware), damit sich der Aufruf im
    strukturierten Logging/Tracing der Bestellung zuordnen laesst - vorher
    wurde hier faelschlich bei jedem Aufruf eine neue, zufaellige uuid4()
    erzeugt, was die Trace-Kette zwischen shop-service und warehouse-service
    zerriss. Der Default None/Fallback auf eine neue uuid4() bleibt nur als
    Absicherung fuer Aufrufe ohne Request-Kontext bestehen.
    """
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
    """Aktualisiert den Lagerbestand eines Produkts im Warehouse-Service.

    Siehe fetch_warehouse_stock() zum Zweck von correlation_id.
    """
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
    """Legt einen initialen Lagerbestand fuer ein neu angelegtes Produkt an.

    Siehe fetch_warehouse_stock() zum Zweck von correlation_id.
    """
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
