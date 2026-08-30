"""Generischer Circuit Breaker fuer Aufrufe des Invoice-Service.

Klassisches Drei-Zustands-Muster:
  CLOSED    - Normalbetrieb, Aufrufe werden durchgelassen.
  OPEN      - Nach zu vielen Fehlern in Folge werden Aufrufe sofort mit
              CircuitBreakerOpenError abgelehnt, OHNE den Invoice-Service
              ueberhaupt zu kontaktieren (schuetzt einen bereits
              ueberlasteten/ausgefallenen Downstream-Service).
  HALF_OPEN - Nach Ablauf von reset_seconds wird testweise wieder EIN Aufruf
              zugelassen; Erfolg -> zurueck zu CLOSED, Fehlschlag -> zurueck
              zu OPEN.

Kennt nichts von HTTP/Invoice-Service - saga.py (request_invoice_with_circuit())
entscheidet, wann before_call()/record_success()/record_failure() aufgerufen
werden, und meldet jede Zustandsaenderung (CircuitTransition) als eigenes
Audit-Snapshot-Event.
"""

from dataclasses import dataclass
from enum import Enum
import time
from typing import Callable


class CircuitState(str, Enum):
    """Die drei moeglichen Zustaende des Breakers (siehe Moduldokumentation)."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    """Wird von before_call() geworfen, wenn ein Aufruf aktuell blockiert wird
    (Zustand OPEN oder HALF_OPEN mit bereits ausgeschoepftem Test-Kontingent)."""

    pass


@dataclass(frozen=True)
class CircuitTransition:
    """Beschreibt einen einzelnen Zustandswechsel - wird von saga.py als
    Audit-Snapshot geloggt (siehe Aufruf-Stellen von before_call() etc.)."""

    previous_state: CircuitState
    state: CircuitState
    failure_count: int
    reason: str


@dataclass
class _Circuit:
    """Interner, veraenderlicher Zustand eines Breakers (privat - nach aussen
    wird nur ueber die state/failure_count-Properties und CircuitTransition
    kommuniziert)."""

    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    # Zeitpunkt (via clock()), zu dem in den Zustand OPEN gewechselt wurde -
    # dient before_call() dazu, zu pruefen, ob reset_seconds bereits
    # verstrichen sind.
    opened_at: float | None = None
    # Zaehlt, wie viele Testaufrufe im aktuellen HALF_OPEN-Zustand bereits
    # zugelassen wurden (begrenzt durch half_open_max_calls).
    half_open_calls: int = 0


class CircuitBreaker:
    """Thread-unsicher und zustandsbehaftet - saga.py haelt pro Prozess genau
    EINE Instanz (Modul-Singleton), da alle Invoice-Aufrufe denselben
    Downstream-Service betreffen."""

    def __init__(
        self,
        failure_threshold: int,
        reset_seconds: float,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_seconds = max(0.0, reset_seconds)
        self.half_open_max_calls = max(1, half_open_max_calls)
        self._clock = clock or time.monotonic
        self._circuit = _Circuit()

    def before_call(self) -> CircuitTransition | None:
        """Vom Aufrufer VOR jedem Invoice-Service-Aufruf zu rufen.

        Wirft CircuitBreakerOpenError, wenn der Aufruf aktuell blockiert
        werden soll (OPEN und Reset-Zeitfenster noch nicht erreicht, oder
        HALF_OPEN mit bereits ausgeschoepftem Testkontingent). Ist das
        Reset-Zeitfenster in OPEN erreicht, wechselt automatisch zu
        HALF_OPEN und laesst GENAU DIESEN einen Aufruf durch (kein
        zusaetzlicher Raise). Gibt eine CircuitTransition zurueck, falls
        sich dabei der Zustand geaendert hat, sonst None.
        """
        if self._circuit.state == CircuitState.OPEN:
            if self._circuit.opened_at is not None and self._clock() - self._circuit.opened_at >= self.reset_seconds:
                return self._transition_to(CircuitState.HALF_OPEN, "RESET_TIMEOUT_REACHED")
            raise CircuitBreakerOpenError("Invoice-Service circuit breaker is OPEN")

        if self._circuit.state == CircuitState.HALF_OPEN:
            if self._circuit.half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerOpenError("Invoice-Service circuit breaker is HALF_OPEN")
            self._circuit.half_open_calls += 1
        return None

    def record_success(self) -> CircuitTransition | None:
        """Nach einem erfolgreichen Aufruf zu rufen.

        In CLOSED wird nur der Fehlerzaehler zurueckgesetzt (kein
        Zustandswechsel noetig/zu melden). In HALF_OPEN (der Testaufruf war
        erfolgreich) oder theoretisch OPEN wird zurueck zu CLOSED
        gewechselt und der Fehlerzaehler ebenfalls zurueckgesetzt.
        """
        if self._circuit.state == CircuitState.CLOSED:
            self._circuit.failures = 0
            return None
        return self._transition_to(CircuitState.CLOSED, "INVOICE_CALL_SUCCEEDED", reset_failures=True)

    def record_failure(self, reason: str) -> CircuitTransition | None:
        """Nach einem fehlgeschlagenen Aufruf zu rufen.

        Ein Fehlschlag in HALF_OPEN (der Testaufruf ist erneut gescheitert)
        oeffnet den Breaker SOFORT wieder, unabhaengig vom Fehlerzaehler.
        In CLOSED wird erst ab Erreichen von failure_threshold aufeinander-
        folgenden Fehlern geoeffnet.
        """
        self._circuit.failures += 1
        if self._circuit.state == CircuitState.HALF_OPEN or self._circuit.failures >= self.failure_threshold:
            return self._transition_to(CircuitState.OPEN, reason)
        return None

    @property
    def state(self) -> CircuitState:
        return self._circuit.state

    @property
    def failure_count(self) -> int:
        return self._circuit.failures

    def _transition_to(
        self,
        state: CircuitState,
        reason: str,
        reset_failures: bool = False,
    ) -> CircuitTransition:
        """Interner Helfer: fuehrt einen Zustandswechsel aus und liefert die
        CircuitTransition, die der Aufrufer als Audit-Snapshot
        loggen kann."""
        previous_state = self._circuit.state
        if state == CircuitState.OPEN:
            # Zeitpunkt merken, ab dem das Reset-Zeitfenster (reset_seconds)
            # laeuft; Testkontingent fuer den naechsten HALF_OPEN-Versuch
            # zuruecksetzen.
            self._circuit.opened_at = self._clock()
            self._circuit.half_open_calls = 0
        elif state == CircuitState.HALF_OPEN:
            # Der Aufruf, der gerade diesen Wechsel ausgeloest hat, zaehlt
            # selbst schon als der erste (und ggf. einzige) Testaufruf.
            self._circuit.half_open_calls = 1
        elif state == CircuitState.CLOSED:
            self._circuit.opened_at = None
            self._circuit.half_open_calls = 0
        self._circuit.state = state
        if reset_failures:
            self._circuit.failures = 0
        return CircuitTransition(previous_state, state, self._circuit.failures, reason)
