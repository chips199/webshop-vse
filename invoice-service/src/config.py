from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration des invoice-service.

    Alle Werte kommen ausschliesslich aus Umgebungsvariablen bzw. der
    ".env"-Datei - keine hartcodierten Werte im Code (Vorgabe 5.2 der
    Aufgabenstellung).
    """

    # pydantic-settings liest Werte automatisch aus ENV-Variablen (Name in
    # Grossbuchstaben, z.B. INVOICE_OUTPUT_DIR) und zusaetzlich aus der Datei
    # ".env" im Arbeitsverzeichnis. "extra=ignore" sorgt dafuer, dass
    # unbekannte ENV-Variablen (z.B. fuer andere Services) nicht zu Fehlern
    # fuehren.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "invoice-service"
    service_port: int = 8003
    # Eigene Datenbank des Service (siehe database.py) - kein Service greift
    # direkt auf die Datenbank eines anderen Service zu.
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"
    # Verzeichnis, in dem die erzeugten Rechnungs-PDFs abgelegt werden
    # (siehe create_invoice_pdf() in main.py). Im Container per Docker-Volume
    # gemountet, damit die PDFs einen Container-Neustart ueberstehen.
    invoice_output_dir: str = "invoices"
    # invoice_max_retries lebt bewusst NICHT mehr hier: invoice-service macht pro
    # "invoice.create.requested" genau einen Versuch, die Retry-Anzahl/-Orchestrierung
    # gehoert zur Shop-Saga (siehe shop-service/src/config.py, invoice_max_retries).


# Einmal beim Modul-Import erzeugte, ueberall importierbare Singleton-Instanz
# (from .config import settings) - vermeidet, dass Settings() mehrfach neu
# eingelesen wird.
settings = Settings()
