# Billing Service

FastAPI service for payment processing through a payment facade. PayPal and
Stripe stubs will be implemented behind the facade in a later phase.

## Local endpoints

- `GET /health`
- `GET /payments/{transactionId}/status`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `PAYMENT_PROVIDER`
