from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration des billing-service.

    Alle Werte kommen ausschliesslich aus Umgebungsvariablen bzw. der
    ".env"-Datei (siehe model_config) - nirgendwo im Code werden Provider,
    URLs o.ae. hartcodiert. Die hier definierten Defaults greifen nur, wenn
    keine passende Umgebungsvariable gesetzt ist (praktisch fuer lokale
    Entwicklung/Tests ohne .env).
    """

    # pydantic-settings liest Werte automatisch aus ENV-Variablen (Name in
    # Grossbuchstaben, z.B. PAYMENT_PROVIDER) und zusaetzlich aus der Datei
    # ".env" im Arbeitsverzeichnis. "extra=ignore" sorgt dafuer, dass
    # unbekannte ENV-Variablen (z.B. fuer andere Services) nicht zu Fehlern
    # fuehren.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "billing-service"
    service_port: int = 8002
    # Verbindung zur (gemeinsam genutzten) PostgreSQL-Datenbank.
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    # Verbindung zum RabbitMQ-Broker fuer Event-/Command-Messaging.
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    # Welcher PaymentAdapter aktiv ist ("stripe" oder "paypal") - siehe
    # get_payment_facade() in payment/facade.py.
    payment_provider: str = "stripe"
    # Ohne gesetzten Stripe-Key laeuft StripeAdapter im lokalen Stub-Modus
    # (kein echter API-Call, siehe adapters.py).
    stripe_secret_key: str | None = None
    stripe_payment_method: str = "pm_card_visa"
    # Ohne beide PayPal-Werte laeuft PayPalAdapter im lokalen Stub-Modus
    # inkl. des asynchronen Webhook-Ablaufs (Bonus 4.4).
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    # Ziel-URL fuer den vom PayPal-Stub selbst ausgeloesten Webhook-Call
    # (siehe PayPalAdapter._send_webhook() in adapters.py).
    async_payment_webhook_url: str = "http://localhost:8002/webhooks/payment-stub"
    # Kuenstliche Verzoegerung, bevor der PayPal-Stub seinen Webhook
    # abschickt - simuliert eine asynchrone Zahlungsbestaetigung.
    async_payment_webhook_delay_seconds: float = 2.0
    # Basis-URL des Frontends, fuer die success_url/cancel_url/return_url,
    # zu denen Stripe/PayPal den Kaeufer nach der Sandbox-Zahlung zurueck-
    # schicken.
    shop_frontend_base_url: str = "http://localhost:3000"
    # Steuerung des Retry-mit-Backoff-Verhaltens der PaymentFacade (siehe
    # PaymentFacade._execute() in payment/facade.py).
    payment_retry_max_attempts: int = 3
    payment_retry_backoff_seconds: float = 0.5


# Einmal beim Modul-Import erzeugte, ueberall importierbare Singleton-Instanz
# (from .config import settings) - vermeidet, dass Settings() mehrfach neu
# eingelesen wird.
settings = Settings()
