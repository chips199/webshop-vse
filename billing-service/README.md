# Billing Service

FastAPI service for payment processing through a payment facade. PayPal, Stripe
and an asynchronous webhook stub are available behind the facade. With sandbox
credentials the adapters call Stripe/PayPal sandbox APIs; without credentials
they fall back to deterministic local stubs.

## Local endpoints

- `GET /health`
- `GET /payments/{transactionId}/status`
- `POST /paypal/orders`
- `POST /paypal/orders/{paypalOrderId}/capture`
- `POST /stripe/sessions`
- `GET /stripe/sessions/{sessionId}`
- `POST /webhooks/payment-stub`

## Configuration

- `SERVICE_NAME`
- `SERVICE_PORT`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `PAYMENT_PROVIDER`
- `STRIPE_SECRET_KEY`
- `STRIPE_PAYMENT_METHOD`
- `PAYPAL_CLIENT_ID`
- `PAYPAL_CLIENT_SECRET`
- `PAYPAL_BASE_URL`
- `ASYNC_PAYMENT_WEBHOOK_URL`
- `ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS`

## Async webhook stub

Set `PAYMENT_PROVIDER=async-stub` to simulate an asynchronous payment provider.
`charge()` returns `PENDING` immediately. After
`ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS`, the stub posts to
`ASYNC_PAYMENT_WEBHOOK_URL`. The Billing-Service then publishes
`billing.payment.succeeded` or `billing.payment.failed` and the existing Saga
continues through RabbitMQ.

For tests, pass `webhookStatus` in the payment metadata:

- `webhookStatus=SUCCEEDED`
- `webhookStatus=FAILED`

## Payment provider extension

Add a new subclass of `PaymentAdapter` with a unique `provider_name`. The class
is registered automatically, so the facade and billing core do not need to be
changed for additional providers.

## Tests

```bash
python -m unittest discover tests
```
