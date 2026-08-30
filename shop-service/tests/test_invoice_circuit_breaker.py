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

    def test_half_open_failure_reopens_circuit(self) -> None:
        current = {"now": 100.0}
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=30, clock=lambda: current["now"])
        breaker.record_failure("FIRST_FAILURE")
        current["now"] = 131.0
        breaker.before_call()

        transition = breaker.record_failure("HALF_OPEN_FAILURE")

        self.assertEqual(transition.previous_state, CircuitState.HALF_OPEN)
        self.assertEqual(transition.state, CircuitState.OPEN)
        self.assertEqual(transition.reason, "HALF_OPEN_FAILURE")
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_closed_success_resets_accumulated_failures(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=30)
        breaker.record_failure("TEMPORARY_FAILURE")
        breaker.record_failure("TEMPORARY_FAILURE")

        transition = breaker.record_success()

        self.assertIsNone(transition)
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.failure_count, 0)

    def test_half_open_blocks_calls_after_test_contingent_is_used(self) -> None:
        current = {"now": 100.0}
        breaker = CircuitBreaker(
            failure_threshold=1,
            reset_seconds=30,
            half_open_max_calls=1,
            clock=lambda: current["now"],
        )
        breaker.record_failure("INVOICE_FAILED")
        current["now"] = 131.0
        breaker.before_call()

        with self.assertRaises(CircuitBreakerOpenError):
            breaker.before_call()


if __name__ == "__main__":
    unittest.main()
