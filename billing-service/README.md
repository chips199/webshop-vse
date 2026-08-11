# Billing Service

FastAPI service for payment processing through a payment facade. PayPal and
Stripe are available behind the facade. With sandbox credentials the adapters
call the Stripe/PayPal sandbox APIs; without credentials they fall back to
deterministic local stubs.

## Prerequisites

- Docker and Docker Compose (recommended - runs this service together with
  PostgreSQL and RabbitMQ)
- Alternatively, for standalone development: Python 3.12, a reachable
  PostgreSQL instance (database `billing_service`) and a reachable RabbitMQ
  instance

## Getting started

Via Docker Compose, from the repository root (starts the full stack):

```bash
docker compose up --build billing-service
```

Standalone, from this directory, against dependencies already running
elsewhere (e.g. via `docker compose up postgres rabbitmq`):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8002
```

The service listens on port `8002` by default (see `SERVICE_PORT` below).
Health check: `GET http://localhost:8002/health`. Interactive API docs:
`http://localhost:8002/docs`. billing-service has no client-facing UI - it is
only reached via RabbitMQ messages published by shop-service (see below).

## Local endpoints

- `GET /health`
- `GET /payments/{transactionId}/status`
- `POST /webhooks/payment-stub`

Billing-service is not exposed to clients directly. It is only reached
through `billing.payment.requested`, `billing.payment.confirm.requested` and
`billing.refund.requested` messages published by shop-service.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
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

## Real sandbox redirect vs. simulated webhook (Bonus 4.4)

With sandbox credentials set, neither adapter resolves `charge()`
synchronously - both always return `PENDING`:

- `StripeAdapter` (with `STRIPE_SECRET_KEY`) creates a real Stripe Checkout
  Session and returns a `redirect_url` to the real hosted payment page.
- `PayPalAdapter` (with `PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET`) creates a
  real PayPal sandbox order and returns a `redirect_url` to the real approval
  page.

Both URLs are built from `SHOP_FRONTEND_BASE_URL`. shop-service exposes the
URL so the frontend can redirect the buyer's browser there. After the buyer
returns, shop-service publishes `billing.payment.confirm.requested`, which
makes billing-service call `getStatus()` (real Stripe session check / PayPal
capture) to resolve the payment.

Only `PayPalAdapter` also simulates this async shape without credentials
(Bonus 4.4): after `ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS`, the stub posts to
itself at `ASYNC_PAYMENT_WEBHOOK_URL` (`POST /webhooks/payment-stub`).
Billing-service then publishes `billing.payment.succeeded` or
`billing.payment.failed` and the existing Saga continues through RabbitMQ.
`StripeAdapter` without credentials stays a simple, immediately successful
local stub.

For tests, pass `webhookStatus` in the payment metadata:

- `webhookStatus=SUCCEEDED`
- `webhookStatus=FAILED`

## Payment provider extension

Add a new subclass of `PaymentAdapter` with a unique `provider_name`. The class
is registered automatically, so the facade and billing core do not need to be
changed for additional providers.

## Tests

```bash
python -m unittest discover tests
```
