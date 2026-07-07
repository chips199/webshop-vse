# ADR 001: Technology Stack

## Status

Accepted

## Context

Das Projekt soll mehrere Microservices, API-Dokumentation, Messaging, Persistenz,
lokales Deployment und spaeter ein Frontend abdecken. Der Stack soll fuer eine
Studierendengruppe gut wartbar und lokal reproduzierbar sein.

## Decision

Wir verwenden:

- Python als Backend-Sprache
- FastAPI fuer alle Backend-Services
- React fuer das spaetere Frontend/Admin-Dashboard
- PostgreSQL fuer relationale Persistenz
- Docker Compose fuer lokales Deployment
- RabbitMQ fuer Messaging
- OpenAPI 3.0 fuer API-Dokumentation
- GitHub fuer Versionierung

## Consequences

FastAPI liefert OpenAPI-Dokumentation direkt aus den Services heraus und ist gut
fuer kleine, klar geschnittene Microservices geeignet. PostgreSQL erfuellt die
Pflichtanforderung einer relationalen Datenbank und passt gut zum
Audit-Snapshot-Modell. Docker Compose macht die Abgabe in einer frischen
Umgebung nachvollziehbar.
