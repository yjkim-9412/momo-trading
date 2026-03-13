import unittest
from unittest.mock import AsyncMock, patch

from core.config import settings
from trading.kis_api import get_overseas_order_list


class OverseasOrderListTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_account_type = settings.KIS_ACCOUNT_TYPE
        self._original_paper_stock = settings.KIS_PAPER_STOCK
        settings.KIS_ACCOUNT_TYPE = "VIRTUAL"
        settings.KIS_PAPER_STOCK = "1234567801"

    def tearDown(self):
        settings.KIS_ACCOUNT_TYPE = self._original_account_type
        settings.KIS_PAPER_STOCK = self._original_paper_stock

    async def test_get_overseas_order_list_includes_required_query_fields(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": []})

        with patch("trading.kis_api._request_paged_json", mock_request):
            await get_overseas_order_list("NASDAQ")

        params = mock_request.await_args.kwargs["params"]
        self.assertIn("ORD_DT", params)
        self.assertIn("ORD_GNO_BRNO", params)
        self.assertIn("ODNO", params)
        self.assertEqual(params["ORD_DT"], "")
        self.assertEqual(params["ORD_GNO_BRNO"], "")
        self.assertEqual(params["ODNO"], "")


if __name__ == "__main__":
    unittest.main()
