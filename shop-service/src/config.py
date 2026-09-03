from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfiguration aus Umgebungsvariablen und der optionalen .env-Datei."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "shop-service"
    service_port: int = 8000
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    # REST-Endpunkte fuer lesende Service-Aufrufe.
    audit_service_url: str = "http://localhost:8004"
    warehouse_service_url: str = "http://localhost:8001"
    # Circuit Breaker fuer die Rechnungserstellung.
    invoice_circuit_breaker_failure_threshold: int = 3
    invoice_circuit_breaker_reset_seconds: float = 30.0
    invoice_circuit_breaker_half_open_max_calls: int = 1
    # Maximale Versuche inklusive Erstversuch.
    invoice_max_retries: int = 3
    # Linearer Backoff in Sekunden.
    invoice_retry_backoff_seconds: float = 0.2
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_session_hours: int = 8
    # In HTTPS-Umgebungen auf True setzen.
    admin_cookie_secure: bool = False
    # Ablageort hochgeladener Produktbilder.
    product_image_upload_dir: str = "/app/uploads/product-images"
    # Oeffentliche Basis-URL fuer hochgeladene Bilder.
    shop_public_base_url: str = "http://localhost:8000"


settings = Settings()
