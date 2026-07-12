# ADR 002: RabbitMQ for Service Communication

## Status

Accepted

## Context

Das Aufgabenblatt fordert asynchrone Kommunikation fuer Domain-Events und gibt
REST fuer einzelne interne Service-Aufrufe vor. Die Gruppe moechte RabbitMQ als
zentrales Kommunikationsmittel verwenden.

## Decision

Interne Service-zu-Service-Kommunikation wird ueber RabbitMQ modelliert. Externe
Clients nutzen weiterhin REST/OpenAPI gegen Shop-Service und Audit-Service.

Commands fordern Arbeit an, Events dokumentieren Ergebnisse. Jeder Message
Envelope enthaelt `messageId`, `correlationId`, `type`, `sourceService`,
`timestamp`, `payload` und `previousEventId`.

Fehler, die retryfaehig sind, werden ebenfalls ueber RabbitMQ sichtbar gemacht,
zum Beispiel durch `invoice.retry.scheduled`. Der Invoice-Service versucht die
PDF-Erzeugung mehrfach und publiziert erst danach ein finales `invoice.failed`.

## Consequences

Diese Entscheidung entkoppelt Services staerker und macht Audit-Events
einheitlich. Sie fuehrt aber bewusst zu einer Abweichung von der REST-Vorgabe im
Aufgabenblatt. Diese Abweichung muss im Bericht und in der Praesentation offen
begruendet werden.
