"""API-Datenmodelle des Audit-Service."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class AuditSnapshot(BaseModel):
    """Ein Audit-Snapshot."""

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
