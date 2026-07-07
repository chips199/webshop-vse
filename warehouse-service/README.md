# Warehouse Service

FastAPI service for stock checks, reservations, cancellation and final stock
commit. Business messages will be consumed from RabbitMQ in the next phase.

## Local endpoint

- `GET /health`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
