from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration des audit-service.

    Alle Werte kommen aus Umgebungsvariablen bzw. der ".env"-Datei.
    audit-service braucht nur Infrastrukturkonfiguration, da er keine
    fach- oder anbieterspezifischen Entscheidungen trifft.
    """

    # pydantic-settings liest Werte automatisch aus ENV-Variablen (Name in
    # Grossbuchstaben, z.B. DATABASE_URL) und zusaetzlich aus der Datei
    # ".env" im Arbeitsverzeichnis. "extra=ignore" sorgt dafuer, dass
    # unbekannte ENV-Variablen (z.B. fuer andere Services) nicht zu Fehlern
    # fuehren.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "audit-service"
    service_port: int = 8004
    # Eigene Datenbank des Service (siehe database.py) - kein Service greift
    # direkt auf die Datenbank eines anderen Service zu.
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    # Verbindung zum RabbitMQ-Broker. audit-service bindet sich auf ALLE
    # Routing-Keys ("#", siehe messaging.py) und publiziert selbst nichts.
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"


# Einmal beim Modul-Import erzeugte, ueberall importierbare Singleton-Instanz
# (from .config import settings) - vermeidet, dass Settings() mehrfach neu
# eingelesen wird.
settings = Settings()
