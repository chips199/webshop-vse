# ADR 003: Payment Facade with PayPal and Stripe Adapters (both async with credentials)

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

- `StripeAdapter` (mit Credentials asynchron per Redirect, sonst sofortiger Stub)
- `PayPalAdapter` (mit Credentials asynchron per Redirect, sonst Stub mit
  simuliertem Webhook - siehe unten)

Der aktive Anbieter wird ueber `PAYMENT_PROVIDER=stripe` oder
`PAYMENT_PROVIDER=paypal` konfiguriert.

Adapter registrieren sich ueber `PaymentAdapter.__init_subclass__` automatisch
mit ihrem `provider_name`. Ein weiterer Anbieter wird deshalb durch eine neue
Adapter-Klasse, passende Konfiguration und Tests ergaenzt. Die Fassade und der
Billing-Kern muessen dafuer nicht geaendert werden.

Wenn Sandbox-Zugangsdaten gesetzt sind, nutzen die Adapter die jeweilige
Sandbox. Ohne Zugangsdaten fallen sie auf lokale Stub-Antworten zurueck, damit
Smoke-Tests und Entwicklung ohne externe Abhaengigkeit reproduzierbar bleiben.

Mit Sandbox-Credentials bestaetigt keiner der beiden Adapter eine Zahlung
synchron in `charge()`: `StripeAdapter` legt eine echte Checkout Session an,
`PayPalAdapter` eine echte PayPal-Order; beide liefern `PENDING` mit einer
`redirect_url` zur echten Freigabeseite zurueck. Erst nachdem der Kaeufer von
dort zurueckkehrt, ruft Billing-Service `getStatus()` auf - das fuehrt bei
Stripe die Session-Pruefung, bei PayPal den echten Capture aus und liefert
SUCCEEDED/FAILED. Damit reicht die bestehende Dreier-Fassade
(`charge`/`refund`/`getStatus`) aus - es war keine vierte Operation fuer
"Zahlung nach Redirect bestaetigen" noetig, `getStatus` uebernimmt diese Rolle
sinnvoll mit.

Nur `PayPalAdapter` bietet zusaetzlich ohne Credentials einen asynchronen Stub:
`charge()`
liefert `PENDING` und plant per `threading.Timer` einen Selbst-Webhook an
`POST /webhooks/payment-stub` nach `ASYNC_PAYMENT_WEBHOOK_DELAY_SECONDS`;
dieser Webhook uebersetzt das Ergebnis wie gehabt in
`billing.payment.succeeded`/`billing.payment.failed`. `StripeAdapter` bleibt
ohne Credentials ein einfacher, sofort erfolgreicher lokaler Stub.

## Consequences

Die Billing-Kernlogik bleibt unabhaengig von Anbieter-spezifischen Typen.
Weitere Anbieter koennen durch neue Adapter und Konfiguration ergaenzt werden,
ohne die Bestelllogik umzubauen.
Tests muessen Anbieterwechsel, Erfolg, Ablehnung und Timeout abdecken.
Der Shop-Service kennt einen zusaetzlichen Zwischenstatus
(`PAYMENT_ACTION_REQUIRED`) und einen zusaetzlichen Endpunkt
(`POST /orders/{orderId}/payment-confirmation`), um den Stripe-/PayPal-Redirect
architekturkonform ueber sich selbst zu vermitteln, statt dass das Frontend
Billing-Service direkt aufruft.
