# Warehouse Service

FastAPI service for stock checks, reservations, cancellation and final stock
commit. Business messages are consumed from RabbitMQ.

## Prerequisites

- Docker and Docker Compose (recommended - runs this service together with
  PostgreSQL and RabbitMQ)
- Alternatively, for standalone development: Python 3.12, a reachable
  PostgreSQL instance (database `warehouse_service`) and a reachable
  RabbitMQ instance

## Getting started

Via Docker Compose, from the repository root (starts the full stack):

```bash
docker compose up --build warehouse-service
```

Standalone, from this directory, against dependencies already running
elsewhere (e.g. via `docker compose up postgres rabbitmq`):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8001
```

The service listens on port `8001` by default (see `SERVICE_PORT` below).
Health check: `GET http://localhost:8001/health`. Interactive API docs:
`http://localhost:8001/docs`.

## Local endpoint

- `GET /health`
- `GET /stock`

## RabbitMQ

- Consumes `warehouse.reserve.requested`
- Publishes `warehouse.reservation.succeeded` or `warehouse.reservation.failed`
- Consumes `warehouse.commit.requested`
- Publishes `warehouse.commit.succeeded`
- Consumes `warehouse.cancel.requested`
- Publishes `warehouse.cancel.succeeded`

## Persistence

The service owns the `warehouse_service` database and creates:

- `warehouse_stock`
- `warehouse_reservations`

Reservations increase `reserved_quantity`. Commits decrease both
`quantity_on_hand` and `reserved_quantity`; cancellations only release the
reserved quantity.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
