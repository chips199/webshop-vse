from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration des shop-service.

    Alle Werte kommen aus Umgebungsvariablen bzw. der ".env"-Datei.
    shop-service deckt mehrere Bereiche ab (Saga-
    Choreografie, Payment-Facade/Circuit-Breaker, Admin-Dashboard), daher
    entsprechend viele Konfigurationswerte.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "shop-service"
    service_port: int = 8000
    # Eigene Datenbank des Service - kein Service greift direkt auf die
    # Datenbank eines anderen Service zu.
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    # Synchrone REST-Aufrufe (nicht ueber RabbitMQ) fuer Lesezugriffe, die die
    # Saga nicht braucht: Audit-Timeline im Admin-Dashboard, Produktkatalog/
    # Lagerbestand-Anzeige.
    audit_service_url: str = "http://localhost:8004"
    warehouse_service_url: str = "http://localhost:8001"
    # Circuit-Breaker-Parameter fuer request_invoice_with_circuit(): nach
    # failure_threshold aufeinanderfolgenden Fehlern OPEN, nach
    # reset_seconds Wartezeit HALF_OPEN, dort maximal half_open_max_calls
    # Testanfragen bevor wieder CLOSED oder erneut OPEN.
    invoice_circuit_breaker_failure_threshold: int = 3
    invoice_circuit_breaker_reset_seconds: float = 30.0
    invoice_circuit_breaker_half_open_max_calls: int = 1
    # Retry-Orchestrierung fuer die Rechnungserstellung in der Shop-Saga.
    # invoice_max_retries: wie oft (inkl. Erstversuch) insgesamt versucht wird,
    # bevor eine Bestellung endgueltig als INVOICE_FAILED markiert wird.
    invoice_max_retries: int = 3
    # Backoff zwischen zwei Versuchen in Sekunden, linear mit der
    # Versuchsnummer multipliziert (0.2s, 0.4s, ...).
    invoice_retry_backoff_seconds: float = 0.2
    # Zugangsdaten und Sitzungsdauer fuer den einzelnen Admin-Account.
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_session_hours: int = 8
    # "Secure"-Flag fuer das Session-Cookie; in Produktion (HTTPS) auf True zu
    # setzen, lokal (HTTP) False, sonst wuerde der Browser das Cookie verwerfen.
    admin_cookie_secure: bool = False
    # Ablageort fuer per Admin-Dashboard hochgeladene Produktbilder (Volume-Mount
    # im Container, siehe docker-compose.yml).
    product_image_upload_dir: str = "/app/uploads/product-images"
    # Basis-URL, unter der shop-service von aussen (Browser) erreichbar ist -
    # wird genutzt, um absolute Bild-URLs fuer hochgeladene Produktbilder zu
    # bauen.
    shop_public_base_url: str = "http://localhost:8000"


# Einmal beim Modul-Import erzeugte, ueberall importierbare Singleton-Instanz.
settings = Settings()
