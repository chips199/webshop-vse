"""HTTP-Router (Router-Schicht) des audit-service.

Keine eigene Service-Schicht noetig: audit-service hat kein Business-Wissen,
die beiden Endpunkte hier lesen daher
direkt aus der Repository-Schicht (database.py) - eine dazwischenliegende
Service-Funktion wuerde hier nur unnoetig delegieren, ohne echte Logik zu
kapseln.
"""

from uuid import UUID

from fastapi import APIRouter

from .config import settings
from .database import get_snapshots_by_correlation_id
from .schemas import AuditTimelineResponse, HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(service=settings.service_name)


@router.get("/audit/orders/{correlationId}", response_model=AuditTimelineResponse)
async def get_order_audit_timeline(correlationId: UUID) -> AuditTimelineResponse:
    """Liefert alle Snapshots einer Bestellung,
    chronologisch sortiert (Sortierung passiert in der SQL-Query, siehe
    get_snapshots_by_correlation_id() in database.py)."""
    rows = list(get_snapshots_by_correlation_id(str(correlationId)))
    return AuditTimelineResponse(correlationId=correlationId, snapshots=rows)
