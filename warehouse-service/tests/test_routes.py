import unittest
from unittest.mock import patch

from fastapi import HTTPException

from src.routes import health, patch_stock, post_stock, stock
from src.schemas import StockCreateRequest, StockUpdateRequest


class WarehouseRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_returns_ok_status(self) -> None:
        response = await health()
        self.assertEqual(response.status, "ok")
        self.assertTrue(response.service)

    async def test_stock_serializes_all_entries(self) -> None:
        rows = [
            {
                "productId": "product-1",
                "quantityOnHand": 5,
                "reservedQuantity": 2,
                "availableQuantity": 3,
                "location": "CPU-A1",
            }
        ]
        with patch("src.routes.list_stock", return_value=rows):
            result = await stock()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].productId, "product-1")
        self.assertEqual(result[0].availableQuantity, 3)

    async def test_post_stock_creates_entry(self) -> None:
        request = StockCreateRequest(productId="product-2", quantityOnHand=10, location="RAM-D1")
        created_row = {
            "productId": "product-2",
            "quantityOnHand": 10,
            "reservedQuantity": 0,
            "availableQuantity": 10,
            "location": "RAM-D1",
        }
        with patch("src.routes.create_stock", return_value=created_row) as create_stock:
            result = await post_stock(request)

        create_stock.assert_called_once_with("product-2", 10, "RAM-D1")
        self.assertEqual(result.productId, "product-2")
        self.assertEqual(result.availableQuantity, 10)

    async def test_patch_stock_updates_existing_entry(self) -> None:
        request = StockUpdateRequest(quantityOnHand=8, location="RAM-D2")
        updated_row = {
            "productId": "product-3",
            "quantityOnHand": 8,
            "reservedQuantity": 1,
            "availableQuantity": 7,
            "location": "RAM-D2",
        }
        with patch("src.routes.update_stock", return_value=updated_row) as update_stock:
            result = await patch_stock("product-3", request)

        update_stock.assert_called_once_with("product-3", 8, "RAM-D2")
        self.assertEqual(result.availableQuantity, 7)

    async def test_patch_stock_raises_404_for_unknown_product(self) -> None:
        request = StockUpdateRequest(quantityOnHand=5, location=None)
        with patch("src.routes.update_stock", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                await patch_stock("unknown-product", request)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_patch_stock_raises_409_when_below_reserved_quantity(self) -> None:
        request = StockUpdateRequest(quantityOnHand=1, location=None)
        with patch("src.routes.update_stock", side_effect=ValueError("quantityOnHand must not be lower than reservedQuantity")):
            with self.assertRaises(HTTPException) as ctx:
                await patch_stock("product-4", request)

        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
