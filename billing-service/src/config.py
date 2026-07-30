from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "billing-service"
    service_port: int = 8002
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    payment_provider: str = "stripe"
    stripe_secret_key: str | None = None
    stripe_payment_method: str = "pm_card_visa"
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    async_payment_webhook_url: str = "http://localhost:8002/webhooks/payment-stub"
    async_payment_webhook_delay_seconds: float = 2.0
    payment_retry_max_attempts: int = 3
    payment_retry_backoff_seconds: float = 0.5


settings = Settings()
