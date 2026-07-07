# Shop Service

FastAPI service that exposes the external order API and will orchestrate the
RabbitMQ-based order saga.

## Local endpoint

- `GET /health`
- `POST /orders` returns a temporary `202 Accepted` response until the saga is implemented.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
