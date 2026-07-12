# ADR 003: Payment Facade with PayPal and Stripe Sandbox Adapters

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

Der aktive Anbieter wird ueber `PAYMENT_PROVIDER=stripe` oder
`PAYMENT_PROVIDER=paypal` konfiguriert.

Adapter registrieren sich ueber `PaymentAdapter.__init_subclass__` automatisch
mit ihrem `provider_name`. Ein weiterer Anbieter wird deshalb durch eine neue
Adapter-Klasse, passende Konfiguration und Tests ergaenzt. Die Fassade und der
Billing-Kern muessen dafuer nicht geaendert werden.

Wenn Sandbox-Zugangsdaten gesetzt sind, nutzen die Adapter die jeweilige
Sandbox. Ohne Zugangsdaten fallen sie auf lokale Stub-Antworten zurueck, damit
Smoke-Tests und Entwicklung ohne externe Abhaengigkeit reproduzierbar bleiben.

## Consequences

Die Billing-Kernlogik bleibt unabhaengig von Anbieter-spezifischen Typen.
Weitere Anbieter koennen durch neue Adapter und Konfiguration ergaenzt werden,
ohne die Bestelllogik umzubauen.
Tests muessen Anbieterwechsel, Erfolg, Ablehnung und Timeout abdecken.
