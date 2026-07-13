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
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_session_hours: int = 8
    admin_cookie_secure: bool = False


settings = Settings()
