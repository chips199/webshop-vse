# Billing Service

FastAPI-Service fuer die Zahlungsabwicklung ueber eine Payment-Facade. Hinter
der Facade stehen PayPal und Stripe. Mit Sandbox-Credentials rufen die
Adapter die echten Stripe-/PayPal-Sandbox-APIs auf; ohne Credentials fallen
sie auf deterministische lokale Stubs zurueck.

## Voraussetzungen

- Docker und Docker Compose (empfohlen - startet diesen Service zusammen mit
  RabbitMQ)
- Alternativ fuer die eigenstaendige Entwicklung: Python 3.12 und eine
  erreichbare RabbitMQ-Instanz. billing-service ist zustandslos und nutzt
  keine Datenbank (keine Persistenzschicht, keine `psycopg`-Abhaengigkeit).

## Erste Schritte

Ueber Docker Compose, aus dem Repository-Root (startet den gesamten Stack):

```bash
docker compose up --build billing-service
```

Eigenstaendig, aus diesem Verzeichnis, gegen ein bereits laufendes RabbitMQ
(z.B. per `docker compose up rabbitmq`):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8002
```

Der Service laeuft standardmaessig auf Port `8002` (siehe `SERVICE_PORT`
unten). Health-Check: `GET http://localhost:8002/health`. Interaktive
API-Doku: `http://localhost:8002/docs`. billing-service hat keine eigene
Oberflaeche fuer Endnutzer - er wird ausschliesslich ueber RabbitMQ-
Nachrichten von shop-service erreicht (siehe unten).

## Lokale Endpunkte

- `GET /health`
- `GET /payments/{transactionId}/status`
- `POST /webhooks/payment-stub`

billing-service ist Clients nicht direkt zugaenglich. Er wird nur ueber die
Nachrichten `billing.payment.requested`, `billing.payment.confirm.requested`
und `billing.refund.requested` erreicht, die von shop-service publiziert
werden.

## Konfiguration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `RABBITMQ_URL`
- `PAYMENT_PROVIDER`
- `STRIPE_SECRET_KEY`
- `STRIPE_PAYMENT_METHOD`
- `PAYPAL_CLIENT_ID`
- `PAYPAL_CLIENT_SECRET`
- `PAYPAL_BASE_URL`
- `SHOP_FRONTEND_BASE_URL`
- `ASYNC_PAYMENT_WEBHOOK_URL`
- `ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS`
- `CORS_ALLOWED_ORIGINS` - kommagetrennte Liste erlaubter Browser-Origins
  (Verteidigung in der Tiefe; das Frontend ruft billing-service nie direkt
  auf, sondern immer ueber shop-service)
- `PAYMENT_RETRY_MAX_ATTEMPTS` - Anzahl Retry-Versuche mit Backoff fuer
  `PaymentFacade`-Aufrufe an den Provider-Adapter
- `PAYMENT_RETRY_BACKOFF_SECONDS` - Basis-Wartezeit zwischen diesen Retries

## Echter Sandbox-Redirect vs. simulierter Webhook

Mit gesetzten Sandbox-Credentials loest keiner der beiden Adapter `charge()`
synchron auf - beide liefern immer `PENDING`:

- `StripeAdapter` (mit `STRIPE_SECRET_KEY`) erstellt eine echte Stripe
  Checkout Session und liefert eine `redirect_url` zur echten gehosteten
  Zahlungsseite.
- `PayPalAdapter` (mit `PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET`) erstellt
  eine echte PayPal-Sandbox-Order und liefert eine `redirect_url` zur echten
  Freigabeseite.

Beide URLs werden aus `SHOP_FRONTEND_BASE_URL` gebaut. shop-service stellt
die URL bereit, damit das Frontend den Browser des Kaeufers dorthin
weiterleiten kann. Nachdem der Kaeufer zurueckkehrt, publiziert shop-service
`billing.payment.confirm.requested`, woraufhin billing-service `getStatus()`
aufruft (echte Stripe-Session-Pruefung / PayPal-Capture), um die Zahlung
abzuschliessen.

Nur `PayPalAdapter` simuliert diesen asynchronen Ablauf zusaetzlich ohne
Credentials: Nach `ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS` sendet
der Stub sich selbst einen Aufruf an `ASYNC_PAYMENT_WEBHOOK_URL`
(`POST /webhooks/payment-stub`). billing-service publiziert daraufhin
`billing.payment.succeeded` oder `billing.payment.failed`, und die
bestehende Saga laeuft ueber RabbitMQ weiter. `StripeAdapter` bleibt ohne
Credentials ein einfacher, sofort erfolgreicher lokaler Stub.

Fuer Tests kann `webhookStatus` in den Payment-Metadaten uebergeben werden:

- `webhookStatus=SUCCEEDED`
- `webhookStatus=FAILED`

## Erweiterung um weitere Payment-Provider

Eine neue Unterklasse von `PaymentAdapter` mit eindeutigem `provider_name`
hinzufuegen. Die Klasse wird automatisch registriert, sodass Facade und
Billing-Kernlogik fuer zusaetzliche Provider nicht geaendert werden muessen.

## Tests

```bash
python -m unittest discover tests
```
