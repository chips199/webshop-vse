# Invoice Service

FastAPI service for invoice generation after successful payments. Business
messages are consumed from RabbitMQ.

## Local endpoints

- `GET /health`
- `GET /invoices/{invoiceId}`

## RabbitMQ

- Consumes `invoice.create.requested`
- Publishes `invoice.retry.scheduled`
- Publishes `invoice.created`
- Publishes `invoice.failed`

Invoices are rendered as retro-styled PDF files and stored in the configured
invoice output directory. Metadata and retry status are stored in the
`invoice_service` PostgreSQL database.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `INVOICE_OUTPUT_DIR`
- `INVOICE_MAX_RETRIES`
