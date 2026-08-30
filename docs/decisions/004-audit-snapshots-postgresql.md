# ADR 004: PostgreSQL for Audit Snapshots

## Status

Accepted

## Context

Audit-Snapshots muessen unveraenderlich und pro `correlationId` chronologisch
abrufbar sein. Die Daten muessen strukturiert, filterbar und dauerhaft
gespeichert werden.

## Decision

Der Audit-Service speichert Snapshots in seiner eigenen PostgreSQL-Datenbank
`audit_service`. Jeder Snapshot wird nur eingefuegt. Updates und Deletes sind
fachlich nicht erlaubt.

Die Snapshot-Daten enthalten mindestens `correlationId`, `eventType`, `service`,
`timestamp`, `payload`, `previousEventId`, `actor` und `statusCode`.

## Consequences

PostgreSQL ermoeglicht robuste chronologische Abfragen und JSON-Payloads. Die
Unveraenderlichkeit ist im Code durch einen reinen Insert-Pfad umgesetzt und
sollte fuer produktionsnaehere Umgebungen zusaetzlich ueber Datenbankrechte
abgesichert werden.
