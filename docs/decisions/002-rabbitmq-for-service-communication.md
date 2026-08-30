# ADR 002: RabbitMQ for Service Communication

## Status

Accepted

## Context

Das System benoetigt asynchrone Domain-Events und eine nachvollziehbare
Kommunikation zwischen den Services. RabbitMQ dient als zentrales
Kommunikationsmittel.

## Decision

Interne Service-zu-Service-Kommunikation wird ueber RabbitMQ modelliert. Externe
Clients nutzen weiterhin REST/OpenAPI gegen Shop-Service und Audit-Service.

Commands fordern Arbeit an, Events dokumentieren Ergebnisse. Jeder Message
Envelope enthaelt `messageId`, `correlationId`, `type`, `sourceService`,
`timestamp`, `payload` und `previousEventId`.

Retryfaehige Fehler werden ebenfalls ueber RabbitMQ sichtbar gemacht, zum
Beispiel durch `invoice.retry.scheduled`. Der Invoice-Service fuehrt pro
`invoice.create.requested` genau einen Versuch aus und publiziert bei einem
Fehler `invoice.failed`. Der Shop-Service plant anhand von Versuchszahl und
Circuit-Breaker-Zustand einen weiteren Versuch oder setzt den Endstatus.

## Consequences

Diese Entscheidung entkoppelt Services und vereinheitlicht Saga- sowie
Audit-Events. Synchrone REST-Aufrufe bleiben auf externe APIs und gezielte
Lesezugriffe beschraenkt.
