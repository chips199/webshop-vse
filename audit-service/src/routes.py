"""HTTP-Endpunkte des Audit-Service."""

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
    """Liefert die chronologische Audit-Timeline einer Bestellung."""
    rows = list(get_snapshots_by_correlation_id(str(correlationId)))
    return AuditTimelineResponse(correlationId=correlationId, snapshots=rows)
