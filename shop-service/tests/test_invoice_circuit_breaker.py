import unittest

from src.resilience import CircuitBreaker, CircuitBreakerOpenError, CircuitState


class InvoiceCircuitBreakerTest(unittest.TestCase):
    def test_opens_after_three_failures(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=30)

        self.assertIsNone(breaker.record_failure("INVOICE_FAILED"))
        self.assertIsNone(breaker.record_failure("INVOICE_FAILED"))
        transition = breaker.record_failure("INVOICE_FAILED")

        self.assertEqual(transition.state, CircuitState.OPEN)
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_allows_half_open_test_after_reset_seconds(self) -> None:
        current = {"now": 100.0}
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=30, clock=lambda: current["now"])

        breaker.record_failure("INVOICE_FAILED")
        with self.assertRaises(CircuitBreakerOpenError):
            breaker.before_call()

        current["now"] = 131.0
        transition = breaker.before_call()

        self.assertEqual(transition.state, CircuitState.HALF_OPEN)

    def test_half_open_success_closes_circuit(self) -> None:
        current = {"now": 100.0}
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=30, clock=lambda: current["now"])
        breaker.record_failure("INVOICE_FAILED")
        current["now"] = 131.0
        breaker.before_call()

        transition = breaker.record_success()

        self.assertEqual(transition.state, CircuitState.CLOSED)
        self.assertEqual(breaker.failure_count, 0)


if __name__ == "__main__":
    unittest.main()
