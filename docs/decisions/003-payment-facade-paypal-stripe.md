# ADR 003: Payment Facade with PayPal, Stripe and Async Stub Adapters

## Status

Accepted

## Context

Der Billing-Service muss mindestens zwei Zahlungsanbieter unter einer
einheitlichen Fassade kapseln. Der aktive Anbieter darf nicht hartcodiert sein.
Fuer lokale Tests sollen deterministische Stubs moeglich bleiben; mit
Sandbox-Credentials sollen Stripe Checkout und PayPal Checkout gegen die echten
Sandbox-APIs laufen.

## Decision

Der Billing-Service enthaelt eine Payment-Fassade mit den Operationen
`charge(orderId, amount, currency)`, `refund(transactionId, amount)` und
`getStatus(transactionId)`.

Die ersten Adapter sind:

- `StripeAdapter`
- `PayPalAdapter`
- `AsyncWebhookStubAdapter`

Der aktive Anbieter wird ueber `PAYMENT_PROVIDER=stripe` oder
`PAYMENT_PROVIDER=paypal` konfiguriert. Fuer die asynchrone Stub-Simulation
wird `PAYMENT_PROVIDER=async-stub` verwendet.

Adapter registrieren sich ueber `PaymentAdapter.__init_subclass__` automatisch
mit ihrem `provider_name`. Ein weiterer Anbieter wird deshalb durch eine neue
Adapter-Klasse, passende Konfiguration und Tests ergaenzt. Die Fassade und der
Billing-Kern muessen dafuer nicht geaendert werden.

Wenn Sandbox-Zugangsdaten gesetzt sind, nutzen die Adapter die jeweilige
Sandbox. Ohne Zugangsdaten fallen sie auf lokale Stub-Antworten zurueck, damit
Smoke-Tests und Entwicklung ohne externe Abhaengigkeit reproduzierbar bleiben.

Der `AsyncWebhookStubAdapter` simuliert Anbieter, die eine Zahlung erst spaeter
per Webhook bestaetigen. `charge()` gibt deshalb `PENDING` zurueck. Nach
`ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS` ruft der Stub den Billing-Endpunkt
`POST /webhooks/payment-stub` auf. Der Billing-Service publiziert danach das
bestehende Saga-Event `billing.payment.succeeded` oder
`billing.payment.failed`.

## Consequences

Die Billing-Kernlogik bleibt unabhaengig von Anbieter-spezifischen Typen.
Weitere Anbieter koennen durch neue Adapter und Konfiguration ergaenzt werden,
ohne die Bestelllogik umzubauen.
Tests muessen Anbieterwechsel, Erfolg, Ablehnung und Timeout abdecken.
