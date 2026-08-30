import unittest
from unittest.mock import patch
from uuid import UUID

from src.routes import get_order_audit_timeline, health


class AuditRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_returns_configured_service_name(self) -> None:
        response = await health()
        self.assertEqual(response.status, "ok")
        self.assertTrue(response.service)

    async def test_get_order_audit_timeline_wraps_snapshots(self) -> None:
        correlation_id = UUID("22222222-2222-2222-2222-222222222222")
        snapshot_row = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "correlationId": correlation_id,
            "eventType": "WAREHOUSE_RESERVATION_SUCCEEDED",
            "service": "warehouse-service",
            "timestamp": "2026-08-23T10:00:00+00:00",
            "payload": {"orderId": "order-1"},
            "previousEventId": None,
            "actor": "warehouse-service",
            "statusCode": "SUCCESS",
        }
        with patch("src.routes.get_snapshots_by_correlation_id", return_value=[snapshot_row]) as get_snapshots:
            response = await get_order_audit_timeline(correlation_id)

        get_snapshots.assert_called_once_with(str(correlation_id))
        self.assertEqual(response.correlationId, correlation_id)
        self.assertEqual(len(response.snapshots), 1)
        self.assertEqual(response.snapshots[0].eventType, "WAREHOUSE_RESERVATION_SUCCEEDED")
        self.assertEqual(response.snapshots[0].statusCode, "SUCCESS")

    async def test_get_order_audit_timeline_returns_empty_list_for_unknown_order(self) -> None:
        correlation_id = UUID("33333333-3333-3333-3333-333333333333")
        with patch("src.routes.get_snapshots_by_correlation_id", return_value=[]):
            response = await get_order_audit_timeline(correlation_id)
        self.assertEqual(response.snapshots, [])
        self.assertEqual(response.correlationId, correlation_id)


if __name__ == "__main__":
    unittest.main()
