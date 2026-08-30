# Qualitaetssicherung und Abnahme

## Ziel

Dieses Dokument beschreibt reproduzierbare Tests, erwartete Endzustaende und
die Pruefung der RabbitMQ-Saga fuer den Onlineshop historischer Computerteile.

## Umfang

- Alle Services muessen per Docker Compose starten.
- Health-Endpunkte aller Backend-Services muessen erreichbar sein.
- Die Weboberflaeche muss unter `http://localhost:3000` erreichbar sein.
- Happy Path und Fehlerpfade muessen ueber dieselbe externe Shop-API testbar
  sein.
- Audit-Timelines muessen fuer jede Bestellung per `correlationId`
  nachvollziehbar sein.

## Abnahmeszenarien

| Szenario | Request-Wert | Erwarteter Endstatus | Wichtige Audit-Events |
| --- | --- | --- | --- |
| Erfolgreiche Bestellung | `happy_path` | `COMPLETED` | `ORDER_CREATED`, `BILLING_PAYMENT_SUCCEEDED`, `INVOICE_CREATED`, `ORDER_COMPLETED` |
| Nicht verfuegbarer Artikel | `out_of_stock` | `OUT_OF_STOCK` | `WAREHOUSE_RESERVATION_FAILED` |
| Zahlung abgelehnt | `payment_failed` | `PAYMENT_FAILED` | `BILLING_PAYMENT_FAILED`, `WAREHOUSE_CANCEL_REQUESTED`, `WAREHOUSE_CANCEL_SUCCEEDED` |
| Rechnung fehlerhaft | `invoice_failed` | `INVOICE_FAILED` (nach drei Versuchen; zwischenzeitlich `INVOICE_RETRY_PENDING`) | `INVOICE_FAILED`, `INVOICE_RETRY_SCHEDULED` (von Shop-Service, zweimal), `INVOICE_CIRCUIT_STATE_CHANGED` nach jedem Fehlversuch (Circuit `OPEN` nach dem dritten) |
| Warehouse-Commit scheitert | `warehouse_commit_failed` | `ROLLBACK_COMPLETED` | `WAREHOUSE_COMMIT_FAILED`, `BILLING_REFUND_REQUESTED`, `BILLING_REFUND_SUCCEEDED`, `ORDER_ROLLBACK_COMPLETED` |

## Manuelle Pruefung

1. Stack starten:

   ```bash
   docker compose up -d --build
   ```

2. Weboberflaeche oeffnen:

   ```text
   http://localhost:3000
   ```

3. Ein Computerteil in den Warenkorb legen.
4. Payment-Anbieter waehlen.
5. Bestellung starten und ueber PayPal- oder Stripe-Sandbox abschliessen.
6. Nach der Rueckleitung die Bestellbestaetigung pruefen.
7. Den Bestellmonitor ueber die geschuetzte Admin-Seite pruefen.

Die Fehlerszenarien werden nicht in der Kundenoberflaeche angeboten. Sie bleiben
fuer Smoke-Tests und technische Abnahme ueber definierte Request-Werte erhalten.

## Automatisierte Smoke-Pruefung

Das Skript `scripts/smoke-test.sh` prueft:

- Health-Endpunkte.
- Produktkatalog.
- Eine Happy-Path-Bestellung bis zum Endstatus `COMPLETED`.
- Vorhandensein einer Audit-Timeline und geschuetzten Admin-Zugriff.

Aufruf:

```bash
bash scripts/smoke-test.sh
```

Die URLs koennen bei Bedarf ueberschrieben werden:

```bash
SHOP_API=http://localhost:8000 AUDIT_API=http://localhost:8004 bash scripts/smoke-test.sh
```

## Automatisierte Integrationstests

Das Skript `scripts/integration-test.sh` deckt die in Abschnitt
"Abnahmeszenarien" genannten Faelle end-to-end ab:

- Happy Path (`happy_path`) bis `COMPLETED`.
- Zahlung abgelehnt (`payment_failed`) bis `PAYMENT_FAILED`, inklusive
  Saga-Kompensation durch Stornierung der Warehouse-Reservierung.
- Lager nicht ausreichend (`out_of_stock`) bis `OUT_OF_STOCK`, inklusive
  Nachweis, dass kein weiterer Aufruf fuer Zahlung oder Rechnung erfolgt.

Jedes Szenario prueft ueber den Admin-Audit-Endpunkt, dass die erwartete
Event-Kette als Audit-Snapshot vorliegt, nicht nur den Endstatus der Bestellung.

Aufruf:

```bash
bash scripts/integration-test.sh
```

## Definition of Done

- `bash scripts/smoke-test.sh` laeuft erfolgreich durch.
- Die Retro-Weboberflaeche kann mindestens eine Bestellung ausloesen.
- Fuer jedes Fehlerszenario ist im Audit sichtbar, welches Event die
  Kompensation oder den Endstatus verursacht hat.
- OpenAPI- und Event-Kontrakte passen zu den beschriebenen Szenarien.
