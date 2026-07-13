# RabbitMQ Event and Command Contracts

## Message Envelope

Alle Commands und Events verwenden denselben Envelope:

```json
{
  "messageId": "uuid",
  "correlationId": "uuid",
  "type": "billing.payment.succeeded",
  "sourceService": "billing-service",
  "timestamp": "2026-07-07T15:30:00Z",
  "payload": {},
  "previousEventId": "uuid-or-null"
}
```

## Commands

| Routing Key | Producer | Consumer | Zweck |
| --- | --- | --- | --- |
| `order.created` | Shop-Service | Audit-Service | Bestellung wurde extern angenommen |
| `warehouse.reserve.requested` | Shop-Service | Warehouse-Service | Bestand pruefen und reservieren |
| `warehouse.cancel.requested` | Shop-Service | Warehouse-Service | Reservierung kompensierend stornieren |
| `warehouse.commit.requested` | Shop-Service | Warehouse-Service | Reservierte Artikel final ausbuchen |
| `billing.payment.requested` | Shop-Service | Billing-Service | Zahlung ueber Payment-Fassade starten |
| `billing.refund.requested` | Shop-Service | Billing-Service | Zahlung kompensierend erstatten |
| `invoice.create.requested` | Shop-Service | Invoice-Service | Rechnung nach erfolgreicher Zahlung erstellen |

## Events

| Routing Key | Producer | Consumer | Zweck |
| --- | --- | --- | --- |
| `warehouse.reservation.succeeded` | Warehouse-Service | Shop-Service, Audit-Service | Reservierung erfolgreich |
| `warehouse.reservation.failed` | Warehouse-Service | Shop-Service, Audit-Service | Bestand nicht ausreichend oder Reservierung fehlgeschlagen |
| `warehouse.cancel.succeeded` | Warehouse-Service | Shop-Service, Audit-Service | Reservierung storniert |
| `warehouse.commit.succeeded` | Warehouse-Service | Shop-Service, Audit-Service | Ware final ausgebucht |
| `warehouse.commit.failed` | Warehouse-Service | Shop-Service, Audit-Service | Ausbuchung fehlgeschlagen |
| `billing.payment.succeeded` | Billing-Service | Shop-Service, Audit-Service | Zahlung erfolgreich |
| `billing.payment.failed` | Billing-Service | Shop-Service, Audit-Service | Zahlung abgelehnt oder fehlgeschlagen |
| `billing.refund.succeeded` | Billing-Service | Shop-Service, Audit-Service | Refund erfolgreich abgeschlossen |
| `billing.refund.failed` | Billing-Service | Shop-Service, Audit-Service | Refund fehlgeschlagen |
| `invoice.created` | Invoice-Service | Shop-Service, Audit-Service | Rechnung erfolgreich erstellt |
| `invoice.retry.scheduled` | Invoice-Service | Audit-Service | Wiederholversuch fuer Rechnungserstellung vorgemerkt |
| `invoice.failed` | Invoice-Service | Shop-Service, Audit-Service | Rechnungserstellung fehlgeschlagen |
| `invoice.circuit.state.changed` | Shop-Service | Audit-Service | Circuit-Breaker-Zustand fuer Invoice-Service hat gewechselt |
| `order.completed` | Shop-Service | Audit-Service | Bestellung erfolgreich abgeschlossen |
| `order.rollback.completed` | Shop-Service | Audit-Service | Kompensation abgeschlossen |

## Payload-Konventionen

Payloads enthalten nur fachliche Daten des jeweiligen Ereignisses. Technische
Metadaten wie `correlationId` und `timestamp` bleiben im Envelope.

Betraege werden als Dezimalzahl mit Waehrung gefuehrt:

```json
{
  "amount": "49.90",
  "currency": "EUR"
}
```

### Beispiel: Zahlung erfolgreich

Bei einem erfolgreichen Payment-Event stehen technische Metadaten im Envelope.
Der Payload enthaelt nur die fachlichen Zahlungsdaten:

```json
{
  "messageId": "c6d4cfe3-bb9d-4ef6-a9fc-9931d4cfd4c7",
  "correlationId": "65c40581-4e0d-4a7f-8e9e-0c79fe412c73",
  "type": "billing.payment.succeeded",
  "sourceService": "billing-service",
  "timestamp": "2026-07-07T15:30:00Z",
  "payload": {
    "orderId": "f102c63a-8321-4e64-8fb6-d95a0b8d1f09",
    "transactionId": "stripe-tx-20260707-0001",
    "provider": "stripe",
    "amount": "49.90",
    "currency": "EUR",
    "paymentStatus": "SUCCEEDED"
  },
  "previousEventId": "ab8d54de-a7b4-49b4-91d2-7166c43f99bd"
}
```

`correlationId`, `timestamp`, `sourceService` und `type` werden nicht im
Payload wiederholt, weil sie bereits fuer jede Message einheitlich im Envelope
stehen.

### Asynchrone Zahlungsbestaetigung

Der Anbieter `async-stub` bestaetigt Zahlungen nicht direkt im Rueckgabewert von
`charge()`. Der erste Aufruf liefert intern `PENDING`; der Billing-Service
publiziert dabei noch kein Payment-Event. Nach der konfigurierten Verzoegerung
sendet der Stub einen HTTP-Webhook an den Billing-Service. Erst dieser Webhook
wird in eines der bestehenden RabbitMQ-Events uebersetzt:

- `billing.payment.succeeded`
- `billing.payment.failed`

Beispiel fuer den internen Webhook-Request:

```json
{
  "orderId": "f102c63a-8321-4e64-8fb6-d95a0b8d1f09",
  "transactionId": "async-stub-f102c63a-8321-4e64-8fb6-d95a0b8d1f09",
  "provider": "async-stub",
  "amount": "49.90",
  "currency": "EUR",
  "status": "SUCCEEDED",
  "correlationId": "65c40581-4e0d-4a7f-8e9e-0c79fe412c73",
  "previousEventId": "ab8d54de-a7b4-49b4-91d2-7166c43f99bd"
}
```

## Fehler und Kompensation

Fehler-Events enthalten einen stabilen `reasonCode` und eine lesbare `message`.
Kompensierende Commands und Events verwenden dieselbe `correlationId` und setzen
`previousEventId` auf das Ereignis, das die Kompensation ausgeloest hat.

Retry-Events enthalten zusaetzlich `attempt` und `maxAttempts`.
Circuit-Breaker-Events enthalten `circuitName`, `previousState`, `state`,
`failureCount` und `reasonCode`, damit im Audit sichtbar bleibt, wann der
Invoice-Service-Circuit nach `OPEN`, `HALF_OPEN` oder `CLOSED` gewechselt ist.
