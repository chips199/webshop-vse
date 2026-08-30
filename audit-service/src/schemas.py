"""Pydantic-Schemas (Response-Modelle) des audit-service.

Reine Datenklassen ohne Verhalten, genutzt von routes.py.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str


class AuditSnapshot(BaseModel):
    """Antwort-Schema fuer einen einzelnen Snapshot."""

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
