from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "invoice-service"
    service_port: int = 8003
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    invoice_output_dir: str = "invoices"
    # invoice_max_retries lebt bewusst NICHT mehr hier: invoice-service macht pro
    # "invoice.create.requested" genau einen Versuch, die Retry-Anzahl/-Orchestrierung
    # gehoert zur Shop-Saga (siehe shop-service/src/config.py, invoice_max_retries).


settings = Settings()
