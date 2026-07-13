# Warehouse Service

FastAPI service for stock checks, reservations, cancellation and final stock
commit. Business messages are consumed from RabbitMQ.

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
