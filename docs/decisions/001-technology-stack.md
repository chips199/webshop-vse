# ADR 001: Technology Stack

## Status

Accepted

## Context

Das Projekt soll mehrere Microservices, API-Dokumentation, Messaging, Persistenz,
lokales Deployment und ein Frontend abdecken. Der Stack soll fuer eine
Studierendengruppe gut wartbar und lokal reproduzierbar sein.

## Decision

Wir verwenden:

- Python als Backend-Sprache
- FastAPI fuer alle Backend-Services
- React fuer Shop-Oberflaeche und Admin-Dashboard
- PostgreSQL fuer relationale Persistenz mit getrennten Datenbanken pro Service
- Docker Compose fuer lokales Deployment
- RabbitMQ fuer Messaging
- OpenAPI 3.0 fuer API-Dokumentation
- GitHub fuer Versionierung

## Consequences

FastAPI liefert OpenAPI-Dokumentation direkt aus den Services heraus und ist gut
fuer kleine, klar geschnittene Microservices geeignet. PostgreSQL erfuellt die
Pflichtanforderung einer relationalen Datenbank und passt gut zum
Audit-Snapshot-Modell. Die Services nutzen getrennte Datenbanknamen
(`shop_service`, `warehouse_service`, `invoice_service`, `audit_service`),
damit Ownership und Migrationen pro Service nachvollziehbar bleiben.
Billing-Service ist bewusst zustandslos und hat keine eigene Datenbank - er
liest Zahlungsstatus bei Bedarf direkt beim Zahlungsanbieter (Stripe/PayPal)
aus statt ihn selbst zu persistieren. Docker Compose macht die Abgabe in
einer frischen Umgebung nachvollziehbar.
