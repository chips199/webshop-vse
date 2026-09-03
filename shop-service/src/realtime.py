"""Thread-sicheres Pub/Sub fuer die SSE-Verbindungen des Admin-Dashboards."""
import queue
import threading
from typing import Any

_subscribers: set[queue.Queue] = set()
_lock = threading.Lock()

# Begrenzte Queue pro Client verhindert blockierende Produzenten.
_MAX_QUEUE_SIZE = 50


def subscribe() -> "queue.Queue[dict[str, Any]]":
    """Registriert eine SSE-Verbindung."""
    q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "queue.Queue[dict[str, Any]]") -> None:
    """Entfernt eine SSE-Verbindung."""
    with _lock:
        _subscribers.discard(q)


def publish(event: dict[str, Any]) -> None:
    """Verteilt ein Ereignis ohne Blockieren an alle Abonnenten."""
    with _lock:
        subscribers = list(_subscribers)
    for q in subscribers:
        try:
            q.put_nowait(event)
        except queue.Full:
            # Ereignis fuer einen langsamen Client verwerfen.
            pass
