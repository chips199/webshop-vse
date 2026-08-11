from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "shop-service"
    service_port: int = 8000
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    audit_service_url: str = "http://localhost:8004"
    warehouse_service_url: str = "http://localhost:8001"
    invoice_circuit_breaker_failure_threshold: int = 3
    invoice_circuit_breaker_reset_seconds: float = 30.0
    invoice_circuit_breaker_half_open_max_calls: int = 1
    # Retry-Orchestrierung fuer die Rechnungserstellung: gehoert zur Shop-Saga
    # (nicht mehr zu invoice-service, siehe schedule_invoice_retry() in main.py).
    # invoice_max_retries: wie oft (inkl. Erstversuch) insgesamt versucht wird,
    # bevor eine Bestellung endgueltig als INVOICE_FAILED markiert wird.
    invoice_max_retries: int = 3
    # Backoff zwischen zwei Versuchen in Sekunden, linear mit der Versuchsnummer
    # multipliziert (0.2s, 0.4s, ...) - entspricht dem Verhalten, das frueher
    # intern in invoice-service lag.
    invoice_retry_backoff_seconds: float = 0.2
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_session_hours: int = 8
    admin_cookie_secure: bool = False
    product_image_upload_dir: str = "/app/uploads/product-images"
    shop_public_base_url: str = "http://localhost:8000"


settings = Settings()
