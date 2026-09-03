from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfiguration aus Umgebungsvariablen und der optionalen .env-Datei."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "audit-service"
    service_port: int = 8004
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"


settings = Settings()
