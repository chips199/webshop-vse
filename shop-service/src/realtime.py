"""In-Process Pub/Sub fuer die Echtzeit-Aktualisierung des Admin-Dashboards.

Bonusaufgabe 4.3 verlangt "Echtzeit-Aktualisierung per Server-Sent Events
oder WebSocket" statt eines manuellen Reloads. Die eigentlichen
Zustandsaenderungen einer Bestellung passieren im RabbitMQ-Consumer-Thread
(handle_saga_message in main.py, synchroner Code in einem eigenen
threading.Thread), die SSE-Verbindungen laufen dagegen im asyncio-Event-Loop
von FastAPI/Uvicorn. Dieses Modul ist die duenne, thread-sichere Bruecke
dazwischen:

- publish() wird aus dem Consumer-Thread aufgerufen (Producer).
- subscribe()/unsubscribe() werden aus dem SSE-Endpunkt aufgerufen (main.py),
  je einmal pro offener Browser-Verbindung.

queue.Queue ist bereits von Haus aus thread-safe, daher reicht ein einfaches
Set aus Queues plus ein Lock fuer das Set selbst - keine zusaetzliche
Locking-Logik fuer put()/get() noetig.
"""
import queue
import threading
from typing import Any

_subscribers: set[queue.Queue] = set()
_lock = threading.Lock()

# Begrenzte Queue-Groesse pro Client: ein sehr langsamer oder haengender
# Browser-Tab soll den Producer (den Saga-Consumer-Thread!) niemals
# blockieren koennen. Laeuft eine Queue voll, werden neue Events fuer genau
# diesen einen Client verworfen (siehe publish()) - alle anderen Clients und
# vor allem die eigentliche Saga-Verarbeitung sind davon nicht betroffen.
_MAX_QUEUE_SIZE = 50


def subscribe() -> "queue.Queue[dict[str, Any]]":
    """Registriert einen neuen Abonnenten (eine offene SSE-Verbindung)."""
    q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "queue.Queue[dict[str, Any]]") -> None:
    """Meldet einen Abonnenten ab (Verbindung geschlossen/Client weg)."""
    with _lock:
        _subscribers.discard(q)


def publish(event: dict[str, Any]) -> None:
    """Verteilt ein Event an alle aktuell verbundenen Admin-Dashboards.

    Wird synchron aus dem Saga-Consumer-Thread aufgerufen - darf deshalb
    niemals blockieren oder eine Exception werfen, die die eigentliche
    Nachrichtenverarbeitung stoeren wuerde.
    """
    with _lock:
        subscribers = list(_subscribers)
    for q in subscribers:
        try:
            q.put_nowait(event)
        except queue.Full:
            # Client haengt/liest nicht schnell genug - Event fuer ihn
            # verwerfen statt den Producer-Thread zu blockieren. Der Client
            # bekommt beim naechsten manuellen Reload trotzdem den
            # aktuellen Stand ueber die normalen REST-Endpunkte.
            pass
