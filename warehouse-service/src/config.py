from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration des warehouse-service.

    Alle Werte kommen aus Umgebungsvariablen bzw. der ".env"-Datei. Die
    eigentlichen Lager-/Bestandsdaten (Seed-Daten,
    Produkt-IDs) liegen bewusst NICHT hier, sondern in database.py, da sie
    keine Umgebungs-/Deploy-spezifische Konfiguration sind.
    """

    # pydantic-settings liest Werte automatisch aus ENV-Variablen (Name in
    # Grossbuchstaben, z.B. DATABASE_URL) und zusaetzlich aus der Datei
    # ".env" im Arbeitsverzeichnis. "extra=ignore" sorgt dafuer, dass
    # unbekannte ENV-Variablen (z.B. fuer andere Services) nicht zu Fehlern
    # fuehren.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "warehouse-service"
    service_port: int = 8001
    # Eigene Datenbank des Service (siehe database.py) - kein Service greift
    # direkt auf die Datenbank eines anderen Service zu.
    database_url: str = "postgresql://webshop:webshop@localhost:5432/webshop"
    rabbitmq_url: str = "amqp://webshop:webshop@localhost:5672/"


# Einmal beim Modul-Import erzeugte, ueberall importierbare Singleton-Instanz
# (from .config import settings) - vermeidet, dass Settings() mehrfach neu
# eingelesen wird.
settings = Settings()
