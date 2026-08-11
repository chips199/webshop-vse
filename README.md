# Retro Parts Webshop

Onlineshop fuer historische Computerteile als Microservice-Projekt fuer Vertiefung Software Engineering. Der Shop nutzt Python/FastAPI im Backend, React im Frontend, PostgreSQL fuer persistente Daten und RabbitMQ fuer asynchrone Service-Kommunikation.

## Technologie-Stack

- Backend: Python 3.12, FastAPI
- Frontend: React, Vite
- Datenbanken: PostgreSQL, getrennte Datenbanken pro Service
- Messaging: RabbitMQ
- Deployment lokal: Docker Compose
- API-Doku: OpenAPI 3.0 je Service
- Payment: Stripe Sandbox, PayPal Sandbox, Async-Payment-Stub
- Logging: strukturierte Logs, Loki, Promtail, Grafana

## Services

| Service | Port | Aufgabe |
| --- | ---: | --- |
| Frontend | 3000 | Shop-Oberflaeche, Checkout, Adminbereich |
| Shop-Service | 8000 | Produktkatalog, Bestellungen, Saga-Koordination, Admin-API |
| Warehouse-Service | 8001 | Bestand, Reservierung, Commit, Storno |
| Billing-Service | 8002 | Payment-Fassade, Stripe, PayPal, Async-Stub |
| Invoice-Service | 8003 | Rechnungserzeugung, PDF-Speicherung, Retry |
| Audit-Service | 8004 | Audit-Snapshots und Timeline |
| RabbitMQ UI | 15672 | Message-Broker-Management |
| Grafana | 3001 | Zentrales Log-Dashboard |

## Schnellstart

Voraussetzungen:

- Docker
- Docker Compose
- Optional: Python 3.12 fuer lokale Checks ohne Container

Projekt starten:

```bash
docker compose up --build
```

Im Hintergrund starten:

```bash
docker compose up -d --build
```

Stoppen:

```bash
docker compose down
```

Volumes ebenfalls loeschen, wenn ein komplett frischer Datenstand gewuenscht ist:

```bash
docker compose down -v
```

## Wichtige URLs

- Shop: http://localhost:3000
- Adminbereich: http://localhost:3000/admin
- Shop API Docs: http://localhost:8000/docs
- Warehouse API Docs: http://localhost:8001/docs
- Billing API Docs: http://localhost:8002/docs
- Invoice API Docs: http://localhost:8003/docs
- Audit API Docs: http://localhost:8004/docs
- RabbitMQ Management: http://localhost:15672
- Grafana: http://localhost:3001

Standard-Logins fuer lokale Entwicklung:

- Shop Admin: `admin` / `admin123`
- RabbitMQ: `webshop` / `webshop`
- Grafana: `admin` / `admin`

## Konfiguration

Payment-Zugangsdaten werden ueber eine lokale `.env` gesetzt. Die Datei ist per `.gitignore` ausgeschlossen und sollte nicht committet werden.

Beispiel:

```env
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PAYMENT_METHOD=pm_card_visa
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS=2
```

Weitere zentrale Variablen sind in `docker-compose.yml` bei den jeweiligen Services dokumentiert.

## Kernfunktionen

- Produktkatalog fuer historische Computerteile
- Warenkorb mit Mengensteuerung und Bestandsgrenzen
- Checkout ueber Stripe Sandbox oder PayPal Sandbox
- Bestellbestaetigung nach erfolgreicher Zahlung
- Saga-basierte Bestellabwicklung ueber RabbitMQ
- Warehouse-Reservierung, Commit und Storno
- Rechnungserzeugung als PDF im Invoice-Service
- Audit-Snapshots pro `correlationId`
- Admin-Dashboard mit Bestellmonitor
- Admin-Artikelverwaltung mit Neuanlage und Bearbeitung
- Separate Warehouse-Adminansicht fuer Mengen und Lagerorte
- Retry und Circuit Breaker fuer Invoice-Anforderung
- Zentrales Log-Management mit Grafana/Loki

## Architektur

Die interne Service-Kommunikation laeuft ueber RabbitMQ. Externe Zugriffe erfolgen ueber HTTP APIs:

- Kunden und Admins nutzen das React-Frontend.
- Das Frontend ruft den Shop-Service fuer Produkte, Bestellungen und Adminfunktionen auf.
- Der Shop-Service koordiniert die Saga und publiziert Commands/Events.
- Warehouse, Billing, Invoice und Audit reagieren auf relevante RabbitMQ-Nachrichten.
- Audit persistiert unveraenderliche Snapshots fuer Nachvollziehbarkeit.

Detaildokumente:

- Architektur: [docs/architecture.md](docs/architecture.md)
- Event-Kontrakte: [docs/event-contracts.md](docs/event-contracts.md)
- Log-Management: [docs/log-management.md](docs/log-management.md)
- ADRs: [docs/decisions](docs/decisions)

## OpenAPI

Jeder Backend-Service besitzt eine eigene OpenAPI-Datei:

- [shop-service/openapi.yaml](shop-service/openapi.yaml)
- [warehouse-service/openapi.yaml](warehouse-service/openapi.yaml)
- [billing-service/openapi.yaml](billing-service/openapi.yaml)
- [invoice-service/openapi.yaml](invoice-service/openapi.yaml)
- [audit-service/openapi.yaml](audit-service/openapi.yaml)

Zur Laufzeit stellt FastAPI zusaetzlich `/docs` und `/openapi.json` bereit.

## Tests

Smoke-Test (Erreichbarkeit aller Services + ein Happy-Path-Durchlauf):

```bash
bash scripts/smoke-test.sh
```

Integrationstests fuer den Bestellprozess (Happy Path + zwei
Fehlerszenarien - Zahlung abgelehnt, Lager nicht ausreichend - inkl.
Pruefung der jeweils erwarteten Audit-Event-Kette):

```bash
bash scripts/integration-test.sh
```

Unit-Tests in Containern:

```bash
docker compose exec -T shop-service python -m unittest discover -s tests
docker compose exec -T billing-service python -m unittest discover -s tests
docker compose exec -T invoice-service python -m unittest discover -s tests
```

Statische Python-Pruefung:

```bash
python3 -m compileall -f shop-service/src warehouse-service/src billing-service/src invoice-service/src audit-service/src
```

OpenAPI-YAML pruefen:

```bash
ruby -e 'require "yaml"; Dir["*-service/openapi.yaml"].each { |path| YAML.load_file(path); puts "OK #{path}" }'
```

## Datenhaltung

PostgreSQL wird lokal als gemeinsamer Container betrieben, aber logisch mit getrennten Datenbanken pro Service:

- `shop_service`
- `warehouse_service`
- `billing_service`
- `invoice_service`
- `audit_service`

Die Initialisierung liegt unter [docker/postgres/init-databases.sql](docker/postgres/init-databases.sql).

## Adminbereich

Der Adminbereich ist unter `/admin` erreichbar und per HttpOnly-Session-Cookie geschuetzt.

Funktionen:

- Bestellungen und Status einsehen
- Audit-Timeline pro Bestellung anzeigen
- Artikel anlegen und bearbeiten
- Warehouse-Mengen und Lagerorte pflegen

## Projektstruktur

```text
.
├── audit-service/
├── billing-service/
├── docker/
├── docs/
├── frontend/
├── invoice-service/
├── scripts/
├── shop-service/
├── warehouse-service/
└── docker-compose.yml
```

## Hinweise

- Interne Kommunikation zwischen Services erfolgt bewusst ueber RabbitMQ.
- REST wird fuer externe APIs, Adminfunktionen, Health Checks und einzelne technische Abfragen genutzt.
- Payment laeuft im lokalen Projekt ueber Sandbox-Modi und Stubs.
- Secrets gehoeren in `.env`, nicht in Git.
