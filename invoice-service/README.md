# Invoice Service

FastAPI service for invoice generation after successful payments. Business
messages are consumed from RabbitMQ.

## Local endpoints

- `GET /health`
- `GET /invoices/{invoiceId}` returns invoice metadata and the PDF download URL.
- `GET /invoices/{invoiceId}/pdf` downloads the generated invoice PDF.

## RabbitMQ

- Consumes `invoice.create.requested`
- Publishes `invoice.created`
- Publishes `invoice.failed`

invoice-service makes exactly **one** attempt per `invoice.create.requested`
message; it no longer retries internally and no longer publishes
`invoice.retry.scheduled` itself. Retry orchestration (how many attempts,
backoff, when to give up) now lives in shop-service's saga, since only
shop-service knows the state of the invoice circuit breaker (see
`shop-service/README.md`). On failure, invoice-service reports back via
`invoice.failed` (including the `attempt` number and the payment fields
shop-service needs to build a follow-up request), and shop-service decides
whether/when to schedule the next attempt.

Invoices are rendered as retro-styled PDF files with order number, transaction,
customer, shipping address, billing address, purchased items and total amount.
They are stored in the configured invoice output directory. Metadata is stored
in the `invoice_service` PostgreSQL database.

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `INVOICE_OUTPUT_DIR`
