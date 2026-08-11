# Audit Service

FastAPI service for immutable audit snapshots.

## Prerequisites

- Docker and Docker Compose (recommended - runs this service together with
  PostgreSQL and RabbitMQ)
- Alternatively, for standalone development: Python 3.12, a reachable
  PostgreSQL instance (database `audit_service`) and a reachable RabbitMQ
  instance

## Getting started

Via Docker Compose, from the repository root (starts the full stack):

```bash
docker compose up --build audit-service
```

Standalone, from this directory, against dependencies already running
elsewhere (e.g. via `docker compose up postgres rabbitmq`):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8004
```

The service listens on port `8004` by default (see `SERVICE_PORT` below).
Health check: `GET http://localhost:8004/health`. Interactive API docs:
`http://localhost:8004/docs`. audit-service binds to every routing key on
the shared exchange, so it also needs RabbitMQ to be reachable at startup
even though it never publishes messages itself.

## Local endpoints

- `GET /health`
- `GET /audit/orders/{correlationId}`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
