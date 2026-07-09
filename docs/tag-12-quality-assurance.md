# Tag 12: Qualitaetssicherung und Abnahme

## Ziel

Tag 12 macht die bisherige Kernimplementierung abnahmefaehig. Im Fokus stehen
reproduzierbare Tests, klare erwartete Endzustaende und eine kurze Anleitung,
wie die RabbitMQ-Saga fuer den Onlineshop historischer Computerteile geprueft
wird.

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
| Rechnung fehlerhaft | `invoice_failed` | `INVOICE_RETRY_PENDING` | `INVOICE_RETRY_SCHEDULED`, `INVOICE_FAILED` |
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
5. Szenario auswaehlen.
6. Bestellung starten.
7. Status und Audit-Timeline im Bestellmonitor beobachten.

## Automatisierte Smoke-Pruefung

Das Skript `scripts/smoke-test.sh` prueft:

- Health-Endpunkte.
- Erstellen einer Bestellung pro Szenario.
- Erreichen des erwarteten Endstatus.
- Vorhandensein einer Audit-Timeline.

Aufruf:

```bash
bash scripts/smoke-test.sh
```

Die URLs koennen bei Bedarf ueberschrieben werden:

```bash
SHOP_API=http://localhost:8000 AUDIT_API=http://localhost:8004 bash scripts/smoke-test.sh
```

## Definition of Done

- `bash scripts/smoke-test.sh` laeuft erfolgreich durch.
- Die Retro-Weboberflaeche kann mindestens eine Bestellung ausloesen.
- Fuer jedes Fehlerszenario ist im Audit sichtbar, welches Event die
  Kompensation oder den Endstatus verursacht hat.
- OpenAPI- und Event-Kontrakte passen zu den implementierten Szenarien.
