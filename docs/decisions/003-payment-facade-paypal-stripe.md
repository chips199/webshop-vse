# ADR 003: Payment Facade with PayPal and Stripe Stubs

## Status

Accepted

## Context

Der Billing-Service muss mindestens zwei Zahlungsanbieter unter einer
einheitlichen Fassade kapseln. Der aktive Anbieter darf nicht hartcodiert sein.

## Decision

Der Billing-Service enthaelt eine Payment-Fassade mit den Operationen
`charge(orderId, amount, currency)`, `refund(transactionId, amount)` und
`getStatus(transactionId)`.

Die ersten Adapter sind:

- `StripeAdapter`
- `PayPalAdapter`

Der aktive Anbieter wird ueber `PAYMENT_PROVIDER=stripe` oder
`PAYMENT_PROVIDER=paypal` konfiguriert.

## Consequences

Die Billing-Kernlogik bleibt unabhaengig von Anbieter-spezifischen Typen.
Weitere Anbieter koennen durch neue Adapter und Konfiguration ergaenzt werden.
Tests muessen Anbieterwechsel, Erfolg, Ablehnung und Timeout abdecken.
