# Warehouse Service

FastAPI service for stock checks, reservations, cancellation and final stock
commit. Business messages are consumed from RabbitMQ.

## Local endpoint

- `GET /health`

## RabbitMQ

- Consumes `warehouse.reserve.requested`
- Publishes `warehouse.reservation.succeeded` or `warehouse.reservation.failed`
- Consumes `warehouse.commit.requested`
- Publishes `warehouse.commit.succeeded`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
