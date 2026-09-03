from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfiguration aus Umgebungsvariablen und der optionalen .env-Datei."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "billing-service"
    service_port: int = 8002
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    # Verfuegbare Anbieter: stripe, paypal.
    payment_provider: str = "stripe"
    # Fehlende Zugangsdaten aktivieren den jeweiligen Stub-Modus.
    stripe_secret_key: str | None = None
    stripe_payment_method: str = "pm_card_visa"
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    # Ziel und Verzoegerung des simulierten PayPal-Webhooks.
    async_payment_webhook_url: str = "http://localhost:8002/webhooks/payment-stub"
    async_payment_webhook_delay_seconds: float = 30.0
    # Ruecksprungziele der Zahlungsanbieter.
    shop_frontend_base_url: str = "http://localhost:3000"
    # Kommagetrennte Liste erlaubter CORS-Origins.
    cors_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Wiederholungsversuche der PaymentFacade.
    payment_retry_max_attempts: int = 3
    payment_retry_backoff_seconds: float = 0.5


settings = Settings()
