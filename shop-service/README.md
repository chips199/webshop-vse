# Shop Service

FastAPI service that exposes the external order API for a shop selling
historical computer parts, for example Intel 8086 CPUs, Commodore 64 SID chips
and IBM Model M keyboards.

The service starts the RabbitMQ-based order saga and stores order status in
PostgreSQL.

## Prerequisites

- Docker and Docker Compose (recommended - runs this service together with
  PostgreSQL, RabbitMQ and the other backend services it depends on)
- Alternatively, for standalone development: Python 3.12, a reachable
  PostgreSQL instance (database `shop_service`) and a reachable RabbitMQ
  instance

## Getting started

Via Docker Compose, from the repository root (starts the full stack):

```bash
docker compose up --build shop-service
```

Standalone, from this directory, against dependencies already running
elsewhere (e.g. via `docker compose up postgres rabbitmq warehouse-service`):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000
```

The service listens on port `8000` by default (see `SERVICE_PORT` below).
Health check: `GET http://localhost:8000/health`. Interactive API docs:
`http://localhost:8000/docs`.

## Local endpoints

- `GET /health`
- `POST /orders` creates a pending order and publishes `order.created` and `warehouse.reserve.requested`
- `GET /orders/{orderId}` returns the current order status

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`

## Example product IDs

- `22222222-2222-2222-2222-222222222222` - Intel 8086 CPU, 1978
- `33333333-3333-3333-3333-333333333333` - Commodore 64 SID 6581 Sound Chip
- `44444444-4444-4444-4444-444444444444` - IBM Model M Keyboard, 1985
