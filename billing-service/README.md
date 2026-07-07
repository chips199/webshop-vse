# Billing Service

FastAPI service for payment processing through a payment facade. PayPal and
Stripe stubs are available behind the facade as deterministic test adapters.

## Local endpoints

- `GET /health`
- `GET /payments/{transactionId}/status`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `PAYMENT_PROVIDER`

## Tests

```bash
python -m unittest discover tests
```
