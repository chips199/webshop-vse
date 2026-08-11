# Invoice Service

FastAPI service for invoice generation after successful payments. Business
messages are consumed from RabbitMQ.

## Prerequisites

- Docker and Docker Compose (recommended - runs this service together with
  PostgreSQL and RabbitMQ)
- Alternatively, for standalone development: Python 3.12, a reachable
  PostgreSQL instance (database `invoice_service`) and a reachable RabbitMQ
  instance

## Getting started

Via Docker Compose, from the repository root (starts the full stack):

```bash
docker compose up --build invoice-service
```

Standalone, from this directory, against dependencies already running
elsewhere (e.g. via `docker compose up postgres rabbitmq`):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8003
```

The service listens on port `8003` by default (see `SERVICE_PORT` below).
Health check: `GET http://localhost:8003/health`. Interactive API docs:
`http://localhost:8003/docs`.

## Local endpoints

- `GET /health`
- `GET /invoices/{invoiceId}` returns invoice metadata and the PDF download URL.
- `GET /invoices/{invoiceId}/pdf` downloads the generated invoice PDF.

## RabbitMQ

- Consumes `invoice.create.requested`
- Publishes `invoice.created`
- Publishes `invoice.failed`

invoice-service makes exactly **one** attempt per `invoice.create.requested`
message; it no longer retries internally and no longer publishes
`invoice.retry.scheduled` itself. Retry orchestration (how many attempts,
backoff, when to give up) now lives in shop-service's saga, since only
shop-service knows the state of the invoice circuit breaker (see
`shop-service/README.md`). On failure, invoice-service reports back via
`invoice.failed` (including the `attempt` number and the payment fields
shop-service needs to build a follow-up request), and shop-service decides
whether/when to schedule the next attempt.

Invoices are rendered as retro-styled PDF files with order number, transaction,
customer, shipping address, billing address, purchased items and total amount.
They are stored in the configured invoice output directory. Metadata is stored
in the `invoice_service` PostgreSQL database.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `INVOICE_OUTPUT_DIR`
