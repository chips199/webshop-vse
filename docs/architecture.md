# Architektur: Microservice-orientierter Online-Shop

## 1. Zielbild

Das System simuliert den Bestellprozess eines Online-Shops mit klar getrennten
Microservices. Die Umsetzung nutzt Python/FastAPI fuer die Backend-Services,
React fuer ein spaeteres Admin-Dashboard, PostgreSQL fuer Persistenz, RabbitMQ
fuer interne Service-Kommunikation und Docker Compose fuer das lokale Deployment.

Die interne Kommunikation wird bewusst event- und command-basiert ueber RabbitMQ
geplant. Damit weicht das Projekt von der Aufgabenblatt-Empfehlung ab, Shop zu
Warehouse und Billing synchron per REST aufzurufen. Diese Entscheidung ist in
`docs/decisions/002-rabbitmq-for-service-communication.md` begruendet.

## 2. Systemkontext

```mermaid
flowchart LR
    Customer[Kunde]
    Admin[Admin]
    Frontend[React Frontend]
    Services[FastAPI Microservices]
    Stripe[Stripe Stub]
    PayPal[PayPal Stub]

    Customer -->|Bestellung aufgeben| Frontend
    Admin -->|Audit-Timeline ansehen| Frontend
    Frontend -->|HTTP/OpenAPI| Services
    Services -->|PaymentAdapter| Stripe
    Services -->|PaymentAdapter| PayPal
```

## 3. Komponenten

```mermaid
flowchart LR
    Customer[Kunde] --> React[React Frontend]
    React --> Shop[Shop-Service<br/>FastAPI]

    Shop <--> Rabbit[(RabbitMQ)]
    Rabbit <--> Warehouse[Warehouse-Service<br/>FastAPI]
    Rabbit <--> Billing[Billing-Service<br/>FastAPI]
    Rabbit <--> Invoice[Invoice-Service<br/>FastAPI]
    Rabbit --> Audit[Audit-Service<br/>FastAPI]

    Billing --> Facade[Payment-Fassade]
    Facade --> Stripe[StripeAdapter Stub]
    Facade --> PayPal[PayPalAdapter Stub]

    Audit --> AuditDb[(PostgreSQL<br/>Audit Snapshots)]
    Shop --> ShopDb[(PostgreSQL<br/>Orders)]
    Warehouse --> WarehouseDb[(PostgreSQL<br/>Stock/Reservations)]
    Billing --> BillingDb[(PostgreSQL<br/>Payments)]
    Invoice --> InvoiceDb[(PostgreSQL<br/>Invoices)]
```

### Verantwortlichkeiten

| Komponente | Verantwortlichkeit |
| --- | --- |
| React Frontend | Externe UI fuer Bestellung und spaeter Admin-Dashboard |
| Shop-Service | Externe Order API, correlationId, Order-Status, Saga-Orchestrierung |
| Warehouse-Service | Bestand pruefen, Reservierung anlegen, stornieren und final ausbuchen |
| Billing-Service | Zahlungsfluss, Payment-Fassade, Refunds |
| Invoice-Service | Rechnungserstellung nach erfolgreicher Zahlung |
| Audit-Service | Unveraenderliche Audit-Snapshots chronologisch bereitstellen |
| RabbitMQ | Commands und Domain-Events zwischen Services |
| PostgreSQL | Persistenz pro Service, mindestens Audit-Snapshot-Speicher |

## 4. Kommunikationsmodell

Externe Clients sprechen REST/OpenAPI mit dem Shop-Service und Audit-Service.
Interne Service-Kommunikation laeuft ueber RabbitMQ. Commands fordern Arbeit an,
Events beschreiben ein bereits eingetretenes Ergebnis.

Alle Messages verwenden ein gemeinsames Envelope:

```json
{
  "messageId": "3ed57c11-faa8-4e8e-bb4c-4c7c1e6cbb7b",
  "correlationId": "65c40581-4e0d-4a7f-8e9e-0c79fe412c73",
  "type": "billing.payment.succeeded",
  "sourceService": "billing-service",
  "timestamp": "2026-07-07T15:30:00Z",
  "payload": {},
  "previousEventId": "0f394842-96e6-48d8-a4fe-7d0a2a49b17c"
}
```

Die verbindlichen Routing Keys sind in `docs/event-contracts.md` dokumentiert.

## 5. Happy Path

```mermaid
sequenceDiagram
    autonumber
    participant Client as React/Client
    participant Shop as Shop-Service
    participant MQ as RabbitMQ
    participant Warehouse as Warehouse-Service
    participant Billing as Billing-Service
    participant Invoice as Invoice-Service
    participant Audit as Audit-Service

    Client->>Shop: POST /orders
    Shop->>Shop: correlationId erzeugen, Order=PENDING
    Shop->>MQ: warehouse.reserve.requested
    Shop->>MQ: audit ORDER_PLACED
    MQ-->>Warehouse: warehouse.reserve.requested
    Warehouse->>Warehouse: Bestand pruefen, Reservierung anlegen
    Warehouse->>MQ: warehouse.reservation.succeeded
    Warehouse->>MQ: audit WAREHOUSE_RESERVED
    MQ-->>Shop: warehouse.reservation.succeeded
    Shop->>MQ: billing.payment.requested
    MQ-->>Billing: billing.payment.requested
    Billing->>Billing: Payment-Fassade charge()
    Billing->>MQ: billing.payment.succeeded
    Billing->>MQ: invoice.create.requested
    Billing->>MQ: audit PAYMENT_SUCCEEDED
    MQ-->>Invoice: invoice.create.requested
    Invoice->>Invoice: PDF-Rechnung erzeugen und speichern
    Invoice->>MQ: invoice.created
    Invoice->>MQ: audit INVOICE_CREATED
    MQ-->>Shop: billing.payment.succeeded
    Shop->>MQ: warehouse.commit.requested
    MQ-->>Warehouse: warehouse.commit.requested
    Warehouse->>Warehouse: Reservierte Artikel ausbuchen
    Warehouse->>MQ: warehouse.commit.succeeded
    MQ-->>Shop: warehouse.commit.succeeded
    Shop->>Shop: Order=COMPLETED
    Shop->>MQ: order.completed
    MQ-->>Audit: alle Audit-Events
    Shop-->>Client: 201 Created
```

## 6. Fehlerszenario: Zahlung abgelehnt

```mermaid
sequenceDiagram
    autonumber
    participant Client as React/Client
    participant Shop as Shop-Service
    participant MQ as RabbitMQ
    participant Warehouse as Warehouse-Service
    participant Billing as Billing-Service
    participant Audit as Audit-Service

    Client->>Shop: POST /orders
    Shop->>MQ: warehouse.reserve.requested
    MQ-->>Warehouse: warehouse.reserve.requested
    Warehouse->>MQ: warehouse.reservation.succeeded
    MQ-->>Shop: warehouse.reservation.succeeded
    Shop->>MQ: billing.payment.requested
    MQ-->>Billing: billing.payment.requested
    Billing->>Billing: Payment-Fassade charge()
    Billing->>MQ: billing.payment.failed
    Billing->>MQ: audit PAYMENT_FAILED
    MQ-->>Shop: billing.payment.failed
    Shop->>MQ: warehouse.cancel.requested
    Shop->>Shop: Order=PAYMENT_FAILED
    MQ-->>Warehouse: warehouse.cancel.requested
    Warehouse->>Warehouse: Reservierung stornieren
    Warehouse->>MQ: warehouse.cancel.succeeded
    Warehouse->>MQ: audit WAREHOUSE_RESERVATION_CANCELLED
    Shop->>MQ: order.rollback.completed
    MQ-->>Audit: alle Audit-Events
    Shop-->>Client: 422 Unprocessable Entity
```

## 7. Payment-Fassade

Der Billing-Service kapselt alle Zahlungsanbieter hinter einer gemeinsamen
Fassade. Der Billing-Kern arbeitet nur mit eigenen Domaenentypen und kennt keine
anbieter-spezifischen Payloads.

Pflichtoperationen:

- `charge(orderId, amount, currency)`
- `refund(transactionId, amount)`
- `getStatus(transactionId)`

Anbieter:

- `StripeAdapter`
- `PayPalAdapter`

Der aktive Anbieter wird per Konfiguration gesetzt, zum Beispiel
`PAYMENT_PROVIDER=stripe` oder `PAYMENT_PROVIDER=paypal`.

## 8. Audit-Snapshots

Jeder relevante Zustandsuebergang erzeugt ein Audit-Event. Der Audit-Service
persistiert daraus unveraenderliche Snapshots in PostgreSQL.

Mindestfelder:

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| correlationId | UUID | Verbindet alle Snapshots einer Bestellung |
| eventType | String | Fachliches Ereignis, z. B. `PAYMENT_FAILED` |
| service | String | Ursprung des Events |
| timestamp | Timestamp UTC | Zeitpunkt des Ereignisses |
| payload | JSON | Relevanter Zustand zum Zeitpunkt des Events |
| previousEventId | UUID/null | Verkettung zum vorherigen Snapshot |
| actor | String | Systemkomponente oder Benutzer |
| statusCode | String | `SUCCESS`, `FAILURE`, `RETRY`, `COMPENSATING`, `COMPENSATED` |

Snapshots werden nur eingefuegt. Updates und Deletes sind fuer die
Snapshot-Tabelle fachlich verboten.

## 9. Erweiterbarkeitsanalyse: vierter Zahlungsanbieter

Ein weiterer Zahlungsanbieter wird hinzugefuegt, indem ein neuer Adapter im
Billing-Service implementiert wird, der das bestehende Payment-Facade-Interface
erfuellt. Anschliessend wird der Anbieter in der Provider-Konfiguration
registriert und per `PAYMENT_PROVIDER` aktivierbar gemacht.

Zu aendern:

- Neuer Adapter, z. B. `billing-service/src/payment/adyen_adapter.py`
- Provider-Registrierung/Konfiguration
- Tests fuer Erfolg, Ablehnung und Timeout des neuen Anbieters

Nicht zu aendern:

- Saga-Orchestrierung im Shop-Service
- RabbitMQ-Command- und Event-Vertraege
- Externe Shop- und Audit-APIs
- Billing-Kernlogik, sofern sie nur gegen die Fassade programmiert

## 10. Offene Punkte fuer Tag 2

- FastAPI-Service-Gerueste erzeugen.
- Docker Compose mit Services, PostgreSQL und RabbitMQ befuellen.
- Gemeinsame Logging- und Correlation-Id-Konventionen als Code vorbereiten.
- Erste Datenbankschemata fuer Audit, Orders, Payments, Stock und Invoices anlegen.
