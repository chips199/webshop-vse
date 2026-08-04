"""Konkrete Zahlungsanbieter-Adapter (Stripe, PayPal) hinter der PaymentFacade.

Dieses Modul ist bewusst NICHT direkt von aussen zu importieren (siehe
payment/__init__.py) - jeder Zugriff soll ueber die PaymentFacade
(facade.py) laufen. Jeder Adapter implementiert dieselbe kleine
Schnittstelle (charge/refund/get_status) aus PaymentAdapter und registriert
sich automatisch selbst, siehe __init_subclass__ unten.
"""

from decimal import Decimal
import json
import logging
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import settings
from .models import PaymentResult, PaymentStatus

logger = logging.getLogger(__name__)


class PaymentAdapter:
    """Basisklasse/Interface fuer alle Zahlungsanbieter-Adapter.

    Definiert die drei Operationen, die jeder Adapter unterstuetzen muss.
    Ein neuer Anbieter braucht nur eine neue Unterklasse mit gesetztem
    provider_name und implementierten Methoden - die Fassade und der
    restliche Billing-Code muessen dafuer nicht angepasst werden (siehe
    Erweiterbarkeitsanalyse in docs/architecture.md).
    """

    provider_name: str
    # Zentrale Registry aller bekannten Adapter, befuellt durch
    # __init_subclass__. get_payment_facade() (facade.py) schlaegt hier den
    # per Konfiguration gewaehlten Provider-Namen nach.
    registry: dict[str, type["PaymentAdapter"]] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        # Wird von Python automatisch bei JEDER Definition einer Unterklasse
        # aufgerufen (also z.B. beim Import von "class StripeAdapter(...)").
        # Traegt die Unterklasse anhand ihres provider_name in die Registry
        # ein - dadurch "meldet" sich ein neuer Adapter selbst an, ohne dass
        # irgendwo eine zentrale if/elif-Kette gepflegt werden muss.
        super().__init_subclass__(**kwargs)
        provider_name = getattr(cls, "provider_name", None)
        if provider_name:
            PaymentAdapter.registry[provider_name] = cls

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
        payment_metadata: dict | None = None,
    ) -> PaymentResult:
        """Stoesst eine neue Zahlung an. Muss von jeder Unterklasse ueberschrieben werden."""
        raise NotImplementedError

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        """Erstattet eine Zahlung. Muss von jeder Unterklasse ueberschrieben werden."""
        raise NotImplementedError

    def get_status(self, transaction_id: str) -> PaymentResult:
        """Fragt den aktuellen Status ab. Muss von jeder Unterklasse ueberschrieben werden."""
        raise NotImplementedError


class StripeAdapter(PaymentAdapter):
    """Stripe ist mit Sandbox-Credentials ebenfalls asynchron per Redirect.

    charge() legt eine echte Stripe Checkout Session an und liefert PENDING
    mit redirect_url zur echten Sandbox-Zahlungsseite. Ohne Credentials bleibt
    Stripe der einfache, sofort erfolgreiche lokale Stub (Bonus 4.4 wird
    ausschliesslich ueber PayPal abgedeckt, siehe PayPalAdapter).
    """

    provider_name = "stripe"

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
        payment_metadata: dict | None = None,
    ) -> PaymentResult:
        """Startet eine Stripe-Zahlung.

        Drei moegliche Ausgaenge: (1) ein Test-Szenario aus payment_metadata
        erzwingt sofort FAILED (siehe _simulated_result), (2) mit Sandbox-
        Key wird eine echte Checkout Session angelegt und PENDING + redirect
        _url zurueckgegeben, (3) ohne Key simuliert der lokale Stub eine
        sofort erfolgreiche Zahlung.
        """
        payment_metadata = payment_metadata or {}
        simulated = _simulated_result(self.provider_name, order_id, payment_metadata)
        if simulated:
            return simulated
        if settings.stripe_secret_key:
            base_url = settings.shop_frontend_base_url.rstrip("/")
            session = self.create_session(
                order_id,
                amount,
                currency,
                success_url=f"{base_url}/checkout?stripe=approved&orderId={order_id}",
                cancel_url=f"{base_url}/checkout?stripe=cancelled&orderId={order_id}",
                customer_email=payment_metadata.get("customerEmail"),
            )
            return PaymentResult(
                transaction_id=session["sessionId"],
                provider=self.provider_name,
                status=PaymentStatus.PENDING,
                reason="Awaiting Stripe checkout completion",
                redirect_url=session.get("checkoutUrl"),
            )
        return PaymentResult(
            transaction_id=f"stripe-{order_id}",
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        """Erstattet eine Zahlung.

        Nur wenn es sich um eine echte Stripe-PaymentIntent-Id ("pi_...")
        handelt UND Sandbox-Credentials konfiguriert sind, wird tatsaechlich
        ein Refund-API-Call gemacht; Stub-Transaktionen ("stripe-...")
        werden nur lokal als REFUNDED markiert. Die "pi_..."-Id kommt aus
        get_status() (dort auf "paymentIntentId" umgeschwenkt, sobald die
        Session bezahlt ist) - die urspruengliche Checkout-Session-Id
        ("cs_...") wuerde Stripes Refund-Endpunkt nicht akzeptieren.
        """
        if settings.stripe_secret_key and transaction_id.startswith("pi_"):
            self._request(
                "https://api.stripe.com/v1/refunds",
                {
                    "payment_intent": transaction_id,
                    "amount": _minor_units(amount),
                },
            )
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.REFUNDED,
        )

    def get_status(self, transaction_id: str) -> PaymentResult:
        """Prueft, ob eine per charge() angelegte Checkout Session bezahlt wurde.

        Ohne Sandbox-Credentials (Stub-Modus) wird direkt SUCCEEDED
        angenommen, da charge() im Stub-Modus ohnehin sofort erfolgreich
        war. Mit Credentials wird die echte Session bei Stripe abgefragt.
        """
        if settings.stripe_secret_key and not transaction_id.startswith("stripe-"):
            # RuntimeError aus retrieve_session() (Netzwerk-/HTTP-Fehler
            # gegen die Stripe-Sandbox) wird hier bewusst NICHT abgefangen,
            # sondern an die PaymentFacade durchgereicht: erst dadurch greift
            # deren Retry-mit-Backoff ueberhaupt fuer get_status(). Nur eine
            # erfolgreich abgefragte, aber fachlich nicht bezahlte Session
            # fuehrt zu einem FAILED-PaymentResult (kein technischer Fehler,
            # daher auch kein Retry-Kandidat).
            session = self.retrieve_session(transaction_id)
            if session.get("paymentStatus") == "paid":
                # Wie PayPalAdapter.get_status() bei Erfolg auf die captureId
                # umschwenkt, geben wir hier auf die PaymentIntent-ID
                # ("pi_...") aus - nicht mehr auf die urspruengliche Session-
                # ID. Nur die PaymentIntent-ID akzeptiert Stripes Refund-
                # Endpunkt (siehe refund() oben); ohne diesen Tausch waere
                # ein spaeterer echter Refund nie moeglich, da die Session-ID
                # dafuer nicht funktioniert.
                return PaymentResult(
                    transaction_id=session.get("paymentIntentId") or transaction_id,
                    provider=self.provider_name,
                    status=PaymentStatus.SUCCEEDED,
                    reason=f"Stripe session {transaction_id} paid",
                    customer=_with_real_content(session.get("customer"), "email", "firstName", "lastName"),
                    shipping_address=_with_real_content(
                        session.get("shippingAddress"), "street", "city", "postalCode"
                    ),
                )
            return PaymentResult(
                transaction_id=transaction_id,
                provider=self.provider_name,
                status=PaymentStatus.FAILED,
                reason=f"Stripe session {transaction_id} not paid (status={session.get('paymentStatus')})",
            )
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def create_session(
        self,
        reference_id: str,
        amount: Decimal,
        currency: str,
        success_url: str | None = None,
        cancel_url: str | None = None,
        customer_email: str | None = None,
        items: list[dict] | None = None,
    ) -> dict:
        """Legt eine echte Stripe Checkout Session an (nur mit Sandbox-Key).

        Baut den x-www-form-urlencoded-Payload, den Stripes REST-API fuer
        "Checkout Sessions" erwartet (verschachtelte Felder wie
        line_items[0][...] werden dafuer als flache Keys mit eckigen
        Klammern kodiert). Fordert explizit Rechnungs- und Lieferadresse an
        (billing_address_collection/shipping_address_collection), damit
        retrieve_session() spaeter echte Kunden-/Adressdaten auslesen kann.
        """
        data = {
            "mode": "payment",
            "success_url": success_url or "http://localhost:3000/checkout?stripe=approved&session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url or "http://localhost:3000/checkout?stripe=cancelled",
            "client_reference_id": reference_id,
            "payment_method_types[0]": "card",
            "billing_address_collection": "required",
            "shipping_address_collection[allowed_countries][0]": "DE",
            "shipping_address_collection[allowed_countries][1]": "AT",
            "shipping_address_collection[allowed_countries][2]": "CH",
            "metadata[referenceId]": reference_id,
        }
        if customer_email:
            data["customer_email"] = customer_email

        # Ohne explizite Artikel-Liste einen einzelnen Sammelposten ueber
        # den Gesamtbetrag anlegen (reicht fuer den Zahlungsvorgang, ohne
        # dass hier der komplette Warenkorb dupliziert werden muesste).
        line_items = items or [
            {
                "name": "Retro Parts Bestellung",
                "amount": amount,
                "quantity": 1,
            }
        ]
        for index, item in enumerate(line_items):
            item_amount = item.get("amount") or item.get("price") or amount
            data[f"line_items[{index}][quantity]"] = int(item.get("quantity", 1))
            data[f"line_items[{index}][price_data][currency]"] = currency.lower()
            data[f"line_items[{index}][price_data][unit_amount]"] = _minor_units(Decimal(str(item_amount)))
            data[f"line_items[{index}][price_data][product_data][name]"] = item.get("name", "Retro Parts Artikel")

        body = self._request("https://api.stripe.com/v1/checkout/sessions", data)
        return {
            "sessionId": body["id"],
            "status": body.get("status", "open"),
            "paymentStatus": body.get("payment_status", "unpaid"),
            "checkoutUrl": body.get("url"),
            "stub": False,
        }

    def retrieve_session(self, session_id: str) -> dict:
        """Fragt eine bestehende Checkout Session bei Stripe ab (GET-Request).

        Liest neben dem Zahlungsstatus auch die vom Kaeufer auf der echten
        Stripe-Seite eingegebenen Kunden-/Lieferdaten aus und normalisiert
        sie (siehe _normalize_stripe_customer/_normalize_stripe_shipping).
        get_status() entscheidet anhand von "paymentStatus", ob die Zahlung
        erfolgreich war.

        Liest zusaetzlich "payment_intent" aus: das ist bei einer bezahlten
        Checkout Session (im "payment"-Modus) die eigentliche PaymentIntent-
        ID ("pi_..."), die fuer einen spaeteren Refund benoetigt wird - die
        Session-ID selbst ("cs_...") akzeptiert Stripes Refund-Endpunkt
        nicht. Ohne diesen Wert waere ein echter Refund ueber refund()
        unten nie moeglich.
        """
        body = self._request(f"https://api.stripe.com/v1/checkout/sessions/{session_id}", {}, method="GET")
        return {
            "sessionId": body["id"],
            "paymentIntentId": body.get("payment_intent"),
            "status": body.get("status"),
            "paymentStatus": body.get("payment_status"),
            "customer": _normalize_stripe_customer(body.get("customer_details", {})),
            "shippingAddress": _normalize_stripe_shipping(_stripe_shipping_details(body)),
            "stub": False,
        }

    def _request(self, url: str, data: dict, method: str = "POST") -> dict:
        """Fuehrt einen authentifizierten HTTP-Request gegen die Stripe-API aus.

        Nutzt bewusst nur die Python-Standardbibliothek (urllib) statt eines
        Stripe-SDKs, um keine zusaetzliche Abhaengigkeit fuer diesen kleinen
        Ausschnitt der API einzufuehren. Jeder Netzwerk-/HTTP-Fehler wird in
        eine RuntimeError uebersetzt - das ist die einzige Exception-Art,
        die Aufrufer hier (charge/refund/get_status) erwarten muessen.
        """
        encoded_data = urlencode(data).encode("utf-8") if method != "GET" else None
        request = Request(
            url,
            data=encoded_data,
            headers={
                "Authorization": f"Bearer {settings.stripe_secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Stripe sandbox request failed: {exc}") from exc


class PayPalAdapter(PaymentAdapter):
    """PayPal ist immer asynchron: charge() liefert nie sofort ein Endergebnis.

    Mit Sandbox-Credentials wird eine echte PayPal-Order angelegt und der
    Kaeufer per redirect_url zur echten Sandbox-Freigabeseite geschickt; das
    Ergebnis kommt erst zurueck, wenn der Aufrufer nach der Rueckleitung
    get_status() (= Capture) aufruft. Ohne Credentials simuliert der Stub
    denselben Ablauf: charge() liefert PENDING, nach einer konfigurierbaren
    Verzoegerung schickt der Stub sich selbst einen Webhook-Request, der das
    Ergebnis auf herkoemmlichem Weg (POST /webhooks/payment-stub) auftreten
    laesst (Bonus 4.4 aus der Aufgabenstellung).
    """

    provider_name = "paypal"

    def charge(
        self,
        order_id: str,
        amount: Decimal,
        currency: str,
        payment_method: str | None = None,
        payment_metadata: dict | None = None,
    ) -> PaymentResult:
        """Startet eine PayPal-Zahlung - liefert NIE sofort ein Endergebnis.

        Drei moegliche Ausgaenge: (1) ein Test-Szenario erzwingt sofort
        FAILED (siehe _simulated_result), (2) mit Sandbox-Credentials wird
        eine echte PayPal-Order angelegt, PENDING + redirect_url zur
        Freigabeseite zurueckgegeben - das eigentliche Ergebnis kommt erst
        ueber einen spaeteren get_status()/capture_order()-Aufruf, (3) ohne
        Credentials simuliert der Stub denselben asynchronen Ablauf selbst:
        PENDING zurueckgeben und einen verzoegerten Webhook an sich selbst
        planen (siehe _schedule_webhook, Bonus 4.4).
        """
        payment_metadata = payment_metadata or {}
        simulated = _simulated_result(self.provider_name, order_id, payment_metadata)
        if simulated:
            return simulated
        if settings.paypal_client_id and settings.paypal_client_secret:
            base_url = settings.shop_frontend_base_url.rstrip("/")
            order = self.create_order(
                order_id,
                amount,
                currency,
                return_url=f"{base_url}/checkout?paypal=approved&orderId={order_id}",
                cancel_url=f"{base_url}/checkout?paypal=cancelled&orderId={order_id}",
            )
            return PaymentResult(
                transaction_id=order["orderId"],
                provider=self.provider_name,
                status=PaymentStatus.PENDING,
                reason="Awaiting PayPal buyer approval",
                redirect_url=order.get("approveUrl"),
            )
        # Stub-Modus (keine Credentials): Test-Szenario kann ueber
        # payment_metadata gezielt einen erfolgreichen oder fehlgeschlagenen
        # Webhook erzwingen (z.B. fuer Frontend-/Saga-Tests des Fehlerpfads).
        transaction_id = f"paypal-{order_id}"
        webhook_status = str(payment_metadata.get("webhookStatus", "SUCCEEDED")).upper()
        if webhook_status not in {"SUCCEEDED", "FAILED"}:
            webhook_status = "SUCCEEDED"
        self._schedule_webhook(
            {
                "orderId": order_id,
                "transactionId": transaction_id,
                "provider": self.provider_name,
                "amount": f"{amount:.2f}",
                "currency": currency,
                "status": webhook_status,
                "correlationId": payment_metadata.get("correlationId"),
                "previousEventId": payment_metadata.get("previousEventId"),
                "reasonCode": payment_metadata.get("webhookReasonCode") or "PAYMENT_DECLINED",
                "message": payment_metadata.get("webhookMessage") or "PayPal stub webhook completed.",
            }
        )
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.PENDING,
            reason="Webhook confirmation pending",
        )

    def refund(self, transaction_id: str, amount: Decimal) -> PaymentResult:
        """Erstattet eine PayPal-Zahlung.

        Bewusst vereinfacht: PayPal hat dafuer eigentlich einen eigenen
        Refund-Endpunkt je Capture-Id, der hier (anders als bei Stripe)
        nicht angebunden ist - es wird immer nur lokal REFUNDED
        zurueckgegeben, ohne echten API-Call.
        """
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.REFUNDED,
        )

    def get_status(self, transaction_id: str) -> PaymentResult:
        """Fuehrt bei PayPal den eigentlichen Capture aus und liefert das Ergebnis.

        Das ist der Moment, in dem bei einer echten Sandbox-Order das Geld
        tatsaechlich eingezogen wird (siehe capture_order()). Ohne
        Credentials (Stub-Modus) wird direkt SUCCEEDED angenommen, da das
        eigentliche Ergebnis in diesem Fall bereits per Webhook
        (POST /webhooks/payment-stub) gemeldet wurde/wird.
        """
        if settings.paypal_client_id and settings.paypal_client_secret and not transaction_id.startswith("paypal-"):
            # Analog zu StripeAdapter.get_status(): RuntimeError aus
            # capture_order() (Netzwerk-/HTTP-Fehler) wird nicht abgefangen,
            # sondern reicht bis zur PaymentFacade durch, damit deren Retry
            # greift. Ein erneuter capture_order()-Aufruf nach einem
            # technischen Fehler ist hier vertretbar: PayPal laesst eine
            # bereits abgeschlossene Order kein zweites Mal capturen (die
            # Sandbox-API antwortet dann mit einem Fehler statt einer
            # zweiten Buchung), das Risiko einer echten Doppelbuchung durch
            # den Retry selbst ist damit gering. Nur eine erfolgreich
            # abgefragte, aber nicht abgeschlossene Order fuehrt zu einem
            # FAILED-PaymentResult (kein technischer Fehler, kein Retry).
            captured = self.capture_order(transaction_id)
            if captured.get("status") == "COMPLETED":
                return PaymentResult(
                    transaction_id=captured.get("captureId", transaction_id),
                    provider=self.provider_name,
                    status=PaymentStatus.SUCCEEDED,
                    reason=f"PayPal order {transaction_id} captured",
                    customer=_with_real_content(captured.get("payer"), "email", "firstName", "lastName"),
                    shipping_address=_with_real_content(
                        captured.get("shippingAddress"), "street", "city", "postalCode"
                    ),
                )
            return PaymentResult(
                transaction_id=transaction_id,
                provider=self.provider_name,
                status=PaymentStatus.FAILED,
                reason=f"PayPal order {transaction_id} not completed (status={captured.get('status')})",
            )
        return PaymentResult(
            transaction_id=transaction_id,
            provider=self.provider_name,
            status=PaymentStatus.SUCCEEDED,
        )

    def create_order(
        self,
        reference_id: str,
        amount: Decimal,
        currency: str,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> dict:
        """Legt eine echte PayPal-Order (v2/checkout/orders, intent=CAPTURE) an.

        "shipping_preference: GET_FROM_FILE" laesst PayPal die vom Kaeufer
        hinterlegte Lieferadresse verwenden, "user_action: PAY_NOW" zeigt
        direkt einen Zahlen-Button statt "Weiter" auf der PayPal-Seite. Die
        "approve"-Rueckgabelink aus der Antwort ist die redirect_url, zu der
        der Kaeufer weitergeleitet wird.
        """
        body = self._paypal_json_request(
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": reference_id,
                        "amount": {
                            "currency_code": currency.upper(),
                            "value": f"{amount:.2f}",
                        },
                    }
                ],
                "application_context": {
                    "brand_name": "Retro Parts Terminal",
                    "shipping_preference": "GET_FROM_FILE",
                    "user_action": "PAY_NOW",
                    "return_url": return_url or "http://localhost:3000/checkout?paypal=approved",
                    "cancel_url": cancel_url or "http://localhost:3000/checkout?paypal=cancelled",
                },
            },
        )
        approve_url = next(
            (link["href"] for link in body.get("links", []) if link.get("rel") == "approve"),
            None,
        )
        return {
            "orderId": body["id"],
            "status": body.get("status", "CREATED"),
            "approveUrl": approve_url,
            "stub": False,
        }

    def capture_order(self, paypal_order_id: str) -> dict:
        """Zieht das Geld fuer eine vom Kaeufer bereits freigegebene Order ein.

        Das ist der eigentliche Zahlungsabschluss bei PayPal - erst hier
        fliesst tatsaechlich Geld. Liest zusaetzlich Payer- und ggf.
        Lieferadressdaten aus der Antwort aus (normalisiert ueber
        _normalize_paypal_payer/_normalize_paypal_shipping), damit
        get_status() diese optional in das PaymentResult uebernehmen kann.
        """
        body = self._paypal_json_request(f"/v2/checkout/orders/{paypal_order_id}/capture", {})
        capture_id = None
        shipping_address = None
        # Eine Order kann mehrere "purchase_units" haben; dieses Projekt legt
        # nur eine an, die Schleife ist trotzdem robust gegenueber mehreren.
        for unit in body.get("purchase_units", []):
            captures = unit.get("payments", {}).get("captures", [])
            if captures:
                capture_id = captures[0].get("id")
            if unit.get("shipping"):
                shipping_address = _normalize_paypal_shipping(unit["shipping"])
        return {
            "orderId": body["id"],
            "captureId": capture_id or body["id"],
            "status": body.get("status", "COMPLETED"),
            "payer": _normalize_paypal_payer(body.get("payer", {})),
            "shippingAddress": shipping_address,
            "stub": False,
        }

    def _schedule_webhook(self, payload: dict) -> None:
        """Plant den verzoegerten Selbst-Webhook fuer den Stub-Modus (Bonus 4.4).

        threading.Timer statt asyncio/Celery, weil der Rest von adapters.py
        ohnehin synchron ist und der billing-service dafuer keine
        zusaetzliche Infrastruktur braucht - fuer eine Sandbox-Simulation
        reicht ein simpler Hintergrund-Timer. daemon=True verhindert, dass
        ein anstehender Timer den Prozess am Beenden hindert.
        """
        delay = max(settings.async_payment_webhook_delay_seconds, 0.0)
        timer = threading.Timer(delay, self._send_webhook, args=(payload,))
        timer.daemon = True
        timer.start()

    def _send_webhook(self, payload: dict) -> None:
        """Schickt den simulierten Webhook-Callback an den eigenen Service.

        Landet bei POST /webhooks/payment-stub in main.py, genau wie es ein
        echter Zahlungsanbieter per HTTP-Callback tun wuerde. Laeuft im
        Timer-Thread (siehe _schedule_webhook) - ein Fehlschlag wird nur
        geloggt, nicht weiter eskaliert, da es hier keinen Aufrufer mehr
        gibt, der eine Exception sinnvoll behandeln koennte.
        """
        request = Request(
            settings.async_payment_webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10):
                return
        except (HTTPError, URLError) as exc:
            logger.warning("PayPal stub webhook failed: %s", exc)

    def _paypal_json_request(self, path: str, payload: dict) -> dict:
        """Fuehrt einen authentifizierten JSON-Request gegen die PayPal-API aus.

        Holt sich vor jedem Aufruf ein frisches OAuth-Access-Token (siehe
        _access_token) - kein Token-Caching, der Sandbox-Zahlungsverkehr in
        diesem Projekt ist selten genug, dass sich das nicht lohnt. Jeder
        Netzwerk-/HTTP-Fehler wird wie bei Stripe in eine RuntimeError
        uebersetzt.
        """
        access_token = self._access_token()
        request = Request(
            f"{settings.paypal_base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"PayPal sandbox request failed: {exc}") from exc

    def _access_token(self) -> str:
        """Holt ein OAuth2-Access-Token via Client-Credentials-Flow.

        Standard-Authentifizierung fuer die PayPal-REST-API: Client-Id und
        -Secret werden HTTP-Basic-kodiert mitgeschickt, die Antwort enthaelt
        ein kurzlebiges Bearer-Token fuer die eigentlichen API-Aufrufe.
        """
        request = Request(
            f"{settings.paypal_base_url}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        import base64

        credentials = f"{settings.paypal_client_id}:{settings.paypal_client_secret}".encode("utf-8")
        request.add_header("Authorization", f"Basic {base64.b64encode(credentials).decode('ascii')}")
        try:
            with urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"PayPal sandbox auth failed: {exc}") from exc
        return body["access_token"]


_PLACEHOLDER_VALUES = {"", "-", "Stripe-Adresse", "PayPal-Adresse"}


def _with_real_content(normalized: dict | None, *keys: str) -> dict | None:
    """Filtert die _normalize_*-Ergebnisse auf echten Inhalt.

    Die _normalize_stripe_*/_normalize_paypal_*-Helfer liefern immer ein
    vollstaendiges Dict zurueck, auch wenn der Anbieter gar keine Kunden-/
    Adressdaten mitgeschickt hat (z.B. PayPal ohne Shipping-Praeferenz) -
    fehlende Werte werden dabei durch "", "-" oder (bei der Strasse) durch
    die Platzhaltertexte "Stripe-Adresse"/"PayPal-Adresse" ersetzt. Ohne
    diesen Filter wuerde get_status() die bereits im Checkout-Formular
    erfassten echten Daten der Order mit diesen Platzhaltern ueberschreiben.
    """
    if not normalized:
        return None
    if any(str(normalized.get(key) or "").strip() not in _PLACEHOLDER_VALUES for key in keys):
        return normalized
    return None


def _minor_units(amount: Decimal) -> int:
    """Wandelt einen Decimal-Betrag (z.B. 49.90) in Cent/kleinste Waehrungseinheit um.

    Stripe und PayPal erwarten Betraege je nach Endpunkt in kleinsten
    Einheiten (Cent) als Integer, um Rundungs-/Gleitkommafehler bei Geld-
    betraegen zu vermeiden.
    """
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _simulated_result(provider: str, order_id: str, payment_metadata: dict) -> PaymentResult | None:
    """Erzwingt fuer Test-/Demo-Zwecke ein deterministisches Fehler-Ergebnis.

    Wird von StripeAdapter.charge()/PayPalAdapter.charge() als erster Schritt
    aufgerufen: steht in payment_metadata["scenario"] "payment_failed" oder
    "payment_timeout", wird direkt (ohne echten oder simulierten API-Call)
    ein FAILED-PaymentResult zurueckgegeben - so lassen sich Fehlerpfade der
    Saga gezielt und reproduzierbar ausloesen. Bei "happy_path" (Default)
    oder unbekanntem Szenario liefert die Funktion None, der Adapter faehrt
    dann mit seinem normalen Ablauf fort.
    """
    scenario = payment_metadata.get("scenario")
    if scenario == "payment_failed":
        return PaymentResult(
            transaction_id=f"{provider}-{order_id}",
            provider=provider,
            status=PaymentStatus.FAILED,
            reason="PAYMENT_DECLINED",
        )
    if scenario == "payment_timeout":
        return PaymentResult(
            transaction_id=f"{provider}-{order_id}",
            provider=provider,
            status=PaymentStatus.FAILED,
            reason="PAYMENT_TIMEOUT",
        )
    return None


def _normalize_paypal_payer(payer: dict) -> dict:
    """Wandelt PayPals "payer"-Objekt in unser einheitliches Kunden-Format um.

    Fehlende Felder werden zu leeren Strings (nicht zu fehlenden Keys), damit
    Aufrufer immer dieselbe Dict-Form bekommen. _with_real_content() in
    get_status() entscheidet anschliessend, ob genug "echter" Inhalt drin
    ist, um die Order-Daten damit zu ueberschreiben.
    """
    name = payer.get("name", {})
    return {
        "firstName": name.get("given_name") or "",
        "lastName": name.get("surname") or "",
        "email": payer.get("email_address") or "",
        "payerId": payer.get("payer_id") or "",
    }


def _normalize_paypal_shipping(shipping: dict) -> dict:
    """Wandelt PayPals "shipping"-Objekt in unser einheitliches Adress-Format um."""
    address = shipping.get("address", {})
    street, house_number = _split_street_and_house_number(address.get("address_line_1") or "")
    return {
        "street": street or address.get("address_line_1") or "PayPal-Adresse",
        "houseNumber": house_number or "-",
        "postalCode": address.get("postal_code") or "-",
        "city": address.get("admin_area_2") or address.get("admin_area_1") or "-",
        "country": address.get("country_code") or "-",
        "recipientName": shipping.get("name", {}).get("full_name") or "",
    }


def _split_street_and_house_number(address_line: str) -> tuple[str, str]:
    """Trennt eine einzeilige Adresse ("Musterstrasse 12") in Strasse und Hausnummer.

    Sowohl PayPal als auch Stripe liefern die Adresszeile als einen
    einzelnen String - unser Order-Format will Strasse/Hausnummer aber
    getrennt (wie im eigenen Checkout-Formular). Heuristik: das letzte durch
    Leerzeichen getrennte "Wort" gilt als Hausnummer, wenn es mindestens
    eine Ziffer enthaelt (z.B. "12", "12a"); sonst wird die ganze Zeile als
    Strasse gewertet und keine Hausnummer erkannt.
    """
    parts = address_line.strip().rsplit(" ", 1)
    if len(parts) == 2 and any(char.isdigit() for char in parts[1]):
        return parts[0], parts[1]
    return address_line.strip(), ""


def _normalize_stripe_customer(customer_details: dict) -> dict:
    """Wandelt Stripes "customer_details"-Objekt in unser einheitliches Kunden-Format um."""
    name = customer_details.get("name") or ""
    first_name, last_name = _split_full_name(name)
    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": customer_details.get("email") or "",
        "phone": customer_details.get("phone") or "",
    }


def _normalize_stripe_shipping(shipping_details: dict) -> dict:
    """Wandelt Stripes Shipping-Details in unser einheitliches Adress-Format um."""
    address = shipping_details.get("address", {})
    street, house_number = _split_street_and_house_number(address.get("line1") or "")
    return {
        "street": street or address.get("line1") or "Stripe-Adresse",
        "houseNumber": house_number or "-",
        "postalCode": address.get("postal_code") or "-",
        "city": address.get("city") or "-",
        "country": address.get("country") or "-",
        "recipientName": shipping_details.get("name") or "",
    }


def _stripe_shipping_details(session: dict) -> dict:
    """Findet die Lieferadresse in einer Stripe-Checkout-Session-Antwort.

    Stripe liefert Adressdaten je nach API-Version/Konfiguration an
    unterschiedlichen Stellen im Response-Body (direktes
    "shipping_details"-Feld, neuer verschachtelt unter
    "collected_information", oder als Fallback nur die Rechnungsadresse aus
    "customer_details"). Diese Funktion probiert alle drei Stellen der
    Reihe nach durch, bevor sie ein leeres Dict zurueckgibt.
    """
    if session.get("shipping_details"):
        return session["shipping_details"]
    collected = session.get("collected_information") or {}
    if collected.get("shipping_details"):
        return collected["shipping_details"]
    customer_details = session.get("customer_details") or {}
    if customer_details.get("address"):
        return {
            "address": customer_details["address"],
            "name": customer_details.get("name") or "",
        }
    return {}


def _split_full_name(name: str) -> tuple[str, str]:
    """Trennt einen vollen Namen ("Grace Hopper") in Vor- und Nachname.

    Einfache Heuristik am ersten Leerzeichen - reicht fuer die ueblichen
    westlichen Vor-/Nachname-Formate, die Stripe hier liefert. Bei einem
    einzelnen Wort (kein Leerzeichen) wird es als Vorname gewertet, der
    Nachname bleibt leer statt geraten.
    """
    parts = name.strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    stripped = name.strip()
    if not stripped:
        # Kein Fantasiename mehr ("Stripe"/"Kunde"): fehlt der Name in den
        # Stripe-Kundendaten, bleibt das Feld leer statt einen Platzhalter
        # vorzutaeuschen, der spaeter faelschlich als "echte Daten" in die
        # Order uebernommen werden koennte (siehe _with_real_content()).
        return "", ""
    return stripped, ""
