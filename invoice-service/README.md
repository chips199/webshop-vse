# Invoice Service

FastAPI service for invoice generation after successful payments. Business
messages are consumed from RabbitMQ.

## Local endpoints

- `GET /health`
- `GET /invoices/{invoiceId}`

## RabbitMQ

- Consumes `invoice.create.requested`
- Publishes `invoice.created`

Invoices are currently written as simple text files in the container-local
`invoices/` directory. This is a PDF-generation placeholder for the core flow.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
