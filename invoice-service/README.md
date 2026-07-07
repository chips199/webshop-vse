# Invoice Service

FastAPI service for invoice generation after successful payments. Business
messages will be consumed from RabbitMQ in the next phase.

## Local endpoints

- `GET /health`
- `GET /invoices/{invoiceId}`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
