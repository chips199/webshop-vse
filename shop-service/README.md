# Shop Service

FastAPI service that exposes the external order API for a shop selling
historical computer parts, for example Intel 8086 CPUs, Commodore 64 SID chips
and IBM Model M keyboards.

The service starts the RabbitMQ-based order saga and stores order status in
PostgreSQL.

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
